"""24/7 agent monitor: scans guard state, finds unhandled anomalies, acts.

Two brains, one loop:
  - policy: deterministic checks over clients, verdicts, blocks and rules
  - llm: optional review of a compact snapshot through the upstream model

Actions are limited to blocking a client and flipping abuse enforcement
settings; every applied action lands in the agent_events audit table.
"""
import json
import re
import time

import httpx

ALLOWED_ACTIONS = {"block_client", "set_abuse_mode", "set_abuse_enforce"}
LLM_TIMEOUT = 8.0
MAX_ACTIONS = 5
VERDICT_WINDOW_S = 900  # only act on verdicts newer than this

IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
KEY_RE = re.compile(r"\b(?:sk|rk|k|key)[-_][A-Za-z0-9_-]{4,}\b")
DURATION_RE = re.compile(r"(\d+)\s*(second|sec|minute|min|hour|hr|day|week)")
NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")
AGENT_SETTING_KEYS = {"agent_enabled", "agent_mode", "agent_interval", "agent_llm"}


def parse_duration(text, default):
    match = DURATION_RE.search(text)
    if not match:
        return default
    amount = int(match.group(1))
    unit = match.group(2)
    factor = {"second": 1, "sec": 1, "minute": 60, "min": 60,
              "hour": 3600, "hr": 3600, "day": 86400, "week": 604800}[unit]
    return amount * factor


class AgentMonitor:
    def __init__(self, guard, abuse, runtime, store):
        self.guard = guard
        self.abuse = abuse
        self.runtime = runtime
        self.store = store
        self.runs = 0
        self.actions_taken = 0
        self.last_run = 0.0
        self.last_error = ""
        self.last_summary = ""
        self._llm_retry_at = 0.0

    # ------------------------------------------------------------ snapshots
    def snapshot(self):
        now = time.time()
        clients = self.abuse.clients(30)
        blocked = self.abuse.blocked_clients()
        abuse_recent = self.store.decisions("abuse", limit=30)
        guard_recent = self.store.decisions("guard", limit=30)
        unblocks = {}
        for event in self.store.block_events(limit=200):
            if event["action"] == "unblock":
                unblocks[event["client"]] = max(unblocks.get(event["client"], 0), event["ts"])
        return {
            "now": now,
            "clients": clients,
            "blocked": blocked,
            "abuse_recent": abuse_recent,
            "guard_recent": guard_recent,
            "rules": self.store.list_rules(),
            "unblocks": unblocks,
            "abuse_mode": self.runtime.abuse_mode,
            "abuse_enforce": self.runtime.abuse_enforce,
            "abuse_threshold": self.runtime.abuse_threshold,
        }

    # --------------------------------------------------------------- policy
    def _verdict_candidates(self, snap):
        """clients with a recent block-worthy verdict that are not blocked."""
        out = []
        seen = set()
        for verdict in snap["abuse_recent"]:
            client = verdict.get("client")
            label = verdict.get("label")
            if not client or client in seen or label not in ("block", "would-block"):
                continue
            if snap["now"] - (verdict.get("ts") or 0) > VERDICT_WINDOW_S:
                continue  # stale verdict, the block has had its chance
            seen.add(client)
            if client in snap["blocked"]:
                continue
            if snap["unblocks"].get(client, 0) > verdict["ts"]:
                continue  # an operator lifted this after the verdict
            out.append(verdict)
        return out

    def policy(self, snap):
        actions = []
        for rule in snap["rules"]:
            expires = rule.get("expires_at") or 0
            if expires and expires <= snap["now"]:
                continue
            if rule["device"] not in snap["blocked"]:
                actions.append({
                    "action": "block_client",
                    "target": rule["device"],
                    "ttl": max(300, int(self.runtime.abuse_block_ttl)),
                    "reason": "blacklist rule is not applied",
                })
        for verdict in self._verdict_candidates(snap):
            actions.append({
                "action": "block_client",
                "target": verdict["client"],
                "ttl": max(300, int(self.runtime.abuse_block_ttl)),
                "reason": "unhandled %s verdict (%.2f, %s)" % (
                    verdict.get("label"), verdict.get("abuse_score") or 0,
                    verdict.get("category") or "unknown"),
            })
        if not snap["abuse_enforce"] and self._verdict_candidates(snap):
            actions.append({
                "action": "set_abuse_enforce",
                "value": True,
                "reason": "enforcement is off while abuse verdicts are unhandled",
            })
        if snap["abuse_mode"] != "block" and any(
                verdict.get("label") == "block" for verdict in snap["abuse_recent"]):
            actions.append({
                "action": "set_abuse_mode",
                "value": "block",
                "reason": "abuse mode is monitor while block verdicts exist",
            })
        return actions

    # ------------------------------------------------------------------ llm
    def _llm_prompt(self, snap):
        compact = {
            "now": int(snap["now"]),
            "abuse_mode": snap["abuse_mode"],
            "abuse_enforce": snap["abuse_enforce"],
            "blocked": list(snap["blocked"])[:20],
            "rules": [{"device": r["device"], "expires_at": r["expires_at"]} for r in snap["rules"][:20]],
            "clients": [
                {"client": c.get("client"), "requests": c.get("total"),
                 "rate_per_min": c.get("rate_per_min"), "label": c.get("label"),
                 "category": c.get("category")}
                for c in snap["clients"][:12]
            ],
            "verdicts": [
                {"client": v.get("client"), "label": v.get("label"),
                 "score": v.get("abuse_score"), "category": v.get("category"),
                 "age_s": int(snap["now"] - v.get("ts", snap["now"]))}
                for v in snap["abuse_recent"][:12]
            ],
        }
        system = (
            "You are a security agent supervising an API guard. Review the snapshot and "
            "return STRICT JSON: {\"actions\": [...]} where each action is one of\n"
            "{\"action\":\"block_client\",\"target\":\"<client id>\",\"ttl\":<seconds 60..86400>,\"reason\":\"...\"}\n"
            "{\"action\":\"set_abuse_mode\",\"value\":\"block\"|\"monitor\",\"reason\":\"...\"}\n"
            "{\"action\":\"set_abuse_enforce\",\"value\":true|false,\"reason\":\"...\"}\n"
            "Only act on real problems in the data. Max 5 actions. Empty list is fine. "
            "Never block a client that is already blocked."
        )
        return system, json.dumps(compact)

    def llm(self, snap):
        if not self.runtime.upstream_key:
            self.last_error = "no upstream key, policy only"
            return None
        system, user = self._llm_prompt(snap)
        try:
            with httpx.Client(timeout=LLM_TIMEOUT) as client:
                response = client.post(
                    self.runtime.upstream_base + "/chat/completions",
                    headers={
                        "Authorization": "Bearer " + self.runtime.upstream_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.runtime.upstream_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0,
                        "max_tokens": 600,
                        "response_format": {"type": "json_object"},
                    },
                )
            if response.status_code != 200:
                self.last_error = "llm http %s: %s" % (response.status_code, response.text[:120])
                return None
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            actions = data.get("actions")
            if not isinstance(actions, list):
                self.last_error = "llm returned no actions list"
                return None
            self.last_error = ""
            return self._validate(actions, snap)
        except Exception as exc:  # network, json, schema: fall back to policy
            self.last_error = "llm failed: %r" % exc
            return None

    def _validate(self, actions, snap):
        known = {c.get("client") for c in snap["clients"]} | set(snap["blocked"])
        known |= {r["device"] for r in snap["rules"]}
        out = []
        for raw in actions[:MAX_ACTIONS]:
            if not isinstance(raw, dict):
                continue
            action = raw.get("action")
            reason = str(raw.get("reason") or "llm review")
            if action == "block_client":
                target = str(raw.get("target") or "")
                if target not in known or target in snap["blocked"]:
                    continue
                try:
                    ttl = int(raw.get("ttl") or 300)
                except (TypeError, ValueError):
                    ttl = 300
                ttl = max(60, min(ttl, 86400))
                out.append({"action": "block_client", "target": target, "ttl": ttl, "reason": reason})
            elif action == "set_abuse_mode" and raw.get("value") in ("block", "monitor"):
                out.append({"action": "set_abuse_mode", "value": raw["value"], "reason": reason})
            elif action == "set_abuse_enforce" and isinstance(raw.get("value"), bool):
                out.append({"action": "set_abuse_enforce", "value": raw["value"], "reason": reason})
        return out

    # --------------------------------------------------------------- execute
    def execute(self, actions, source, mode):
        applied = []
        for action in actions:
            kind = action["action"]
            if kind == "block_client":
                target = action["target"]
                ttl = int(action["ttl"])
                self.abuse.block_client(target, ttl, category="agent", score=1.0)
                self.store.log_block(target, "block", "agent", 1.0, until=time.time() + ttl)
            elif kind == "blacklist_device":
                device = action["device"]
                ttl = int(action.get("ttl") or 0)
                self.store.add_rule(device, action.get("reason") or "agent command", ttl)
                block_ttl = ttl if ttl > 0 else 30 * 86400
                self.abuse.block_client(device, block_ttl, category="device_rule", score=1.0)
                self.store.log_block(device, "block", "device_rule", 1.0, until=time.time() + block_ttl)
            elif kind == "unblacklist_device":
                device = action["device"]
                self.store.remove_rule(device)
                self.abuse.clear_block(device)
                self.store.log_block(device, "unblock")
            elif kind == "unblacklist_all":
                cleared = 0
                for rule in self.store.list_rules():
                    self.store.remove_rule(rule["device"])
                    self.abuse.clear_block(rule["device"])
                    self.store.log_block(rule["device"], "unblock")
                    cleared += 1
                action["cleared"] = cleared
            elif kind == "set_abuse_mode":
                self.store.set_many({"abuse_mode": action["value"]})
                self.runtime.load()
            elif kind == "set_abuse_enforce":
                self.store.set_many({"abuse_enforce": "1" if action["value"] else "0"})
                self.runtime.load()
            elif kind == "set_guard_mode":
                self.store.set_many({"guard_mode": action["value"]})
                self.runtime.load()
            elif kind == "set_abuse_threshold":
                value = min(1.0, max(0.01, float(action["value"])))
                self.store.set_many({"abuse_threshold": str(value)})
                self.runtime.load()
            elif kind == "set_guard_threshold":
                value = min(1.0, max(0.01, float(action["value"])))
                self.store.set_many({"guard_threshold_" + action["key"]: str(value)})
                self.runtime.load()
            elif kind == "set_abuse_block_ttl":
                value = min(86400, max(5, int(action["value"])))
                self.store.set_many({"abuse_block_ttl": str(value)})
                self.runtime.load()
            elif kind == "set_retention":
                value = min(365, max(1, int(action["value"])))
                self.store.set_many({"retention_days": str(value)})
                self.runtime.load()
            elif kind == "set_agent_mode":
                self.store.set_many({"agent_mode": action["value"]})
                self.runtime.load()
            elif kind == "set_agent_interval":
                value = min(3600, max(5, int(action["value"])))
                self.store.set_many({"agent_interval": str(value)})
                self.runtime.load()
            elif kind == "set_agent_enabled":
                self.store.set_many({"agent_enabled": "1" if action["value"] else "0"})
                self.runtime.load()
            elif kind == "scan_now":
                applied.append(action)
                continue
            else:
                continue
            target = action.get("target") or action.get("device") or action.get("value")
            detail = json.dumps({k: v for k, v in action.items() if k not in ("action", "target", "device", "reason")})
            self.store.log_agent(mode, source, kind, str(target), action.get("reason", ""), detail)
            self.actions_taken += 1
            applied.append(action)
        return applied

    # -------------------------------------------------------------- commands
    def parse_commands(self, message):
        """Deterministic command parser for the chat. Returns (actions, note)."""
        text = message.strip()
        low = text.lower()
        actions = []
        note = ""

        def device_from_text():
            match = IP_RE.search(text)
            if match:
                return match.group(0)
            match = KEY_RE.search(text)
            return match.group(0) if match else ""

        device = device_from_text()
        block_words = ("block", "blacklist", "ban", "deny", "add", "drop")
        unblock_words = ("unblock", "unban", "whitelist", "allow", "remove")
        has_unblock = any(word in low for word in unblock_words)
        has_block = any(word in low for word in block_words)
        wants_all = any(word in low for word in (" all ", " every ", "clear the blacklist", "clear blacklist"))
        if has_unblock and wants_all:
            actions.append({"action": "unblacklist_all", "reason": "chat command"})
        elif device and has_unblock:
            actions.append({"action": "unblacklist_device", "device": device})
        elif device and has_block:
            ttl = 0 if ("blacklist" in low or "permanent" in low) else parse_duration(low, 3600)
            actions.append({"action": "blacklist_device", "device": device, "ttl": ttl})
        elif not device and (has_block or has_unblock) and "mode" not in low and "threshold" not in low:
            return [], "I need the IP address or API key id, for example: block 203.0.113.9"

        if "enforcement" in low:
            if re.search(r"(off|disable|stop|false)", low):
                actions.append({"action": "set_abuse_enforce", "value": False, "reason": "chat command"})
            elif re.search(r"(on|enable|start|true)", low):
                actions.append({"action": "set_abuse_enforce", "value": True, "reason": "chat command"})
        if ("guard mode" in low or "guard to" in low):
            target = "block" if "block" in low else "monitor" if "monitor" in low else ""
            if target:
                actions.append({"action": "set_guard_mode", "value": target, "reason": "chat command"})
        if "abuse mode" in low or "mode to block" in low or "mode to monitor" in low:
            target = "block" if "block" in low else "monitor" if "monitor" in low else ""
            if target:
                actions.append({"action": "set_abuse_mode", "value": target, "reason": "chat command"})
        if "agent mode" in low:
            target = "act" if "act" in low else "monitor" if "monitor" in low else ""
            if target:
                actions.append({"action": "set_agent_mode", "value": target, "reason": "chat command"})
        if "interval" in low and "agent" in low:
            match = NUMBER_RE.search(low)
            if match:
                actions.append({"action": "set_agent_interval", "value": int(float(match.group(1))), "reason": "chat command"})
        if re.search(r"(pause|disable|stop) (the )?agent", low):
            actions.append({"action": "set_agent_enabled", "value": False, "reason": "chat command"})
        if re.search(r"(resume|enable|start) (the )?agent", low):
            actions.append({"action": "set_agent_enabled", "value": True, "reason": "chat command"})
        if "threshold" in low:
            match = NUMBER_RE.search(low)
            if match:
                value = float(match.group(1))
                if "prompt" in low:
                    actions.append({"action": "set_guard_threshold", "key": "prompt_injection", "value": value, "reason": "chat command"})
                elif "hidden" in low:
                    actions.append({"action": "set_guard_threshold", "key": "hidden_instructions", "value": value, "reason": "chat command"})
                elif "secret" in low:
                    actions.append({"action": "set_guard_threshold", "key": "secret_request", "value": value, "reason": "chat command"})
                else:
                    actions.append({"action": "set_abuse_threshold", "value": value, "reason": "chat command"})
        if "ttl" in low or "block ttl" in low:
            match = NUMBER_RE.search(low)
            if match:
                ttl = parse_duration(low, int(float(match.group(1))))
                actions.append({"action": "set_abuse_block_ttl", "value": ttl, "reason": "chat command"})
        if "retention" in low:
            match = NUMBER_RE.search(low)
            if match:
                actions.append({"action": "set_retention", "value": int(float(match.group(1))), "reason": "chat command"})
        if re.search(r"(scan|run|check) (now|a scan|the scan)", low):
            actions.append({"action": "scan_now", "reason": "chat command"})

        if actions:
            return actions, note
        return [], ""

    def verify_commands(self, actions, snap):
        lines = []
        for action in actions:
            kind = action["action"]
            if kind == "blacklist_device":
                device = action["device"]
                state = "blocked" if device in snap["blocked"] else "pending sync"
                in_rules = any(rule["device"] == device for rule in snap["rules"])
                lines.append("- %s: %s, rule %s" % (
                    device, state, "created" if in_rules else "missing"))
            elif kind == "unblacklist_device":
                device = action["device"]
                cleared = device not in snap["blocked"]
                lines.append("- %s: %s" % (device, "unblocked" if cleared else "still blocked"))
            elif kind == "unblacklist_all":
                lines.append("- blacklist cleared (%d rule%s removed)" % (
                    action.get("cleared", 0), "" if action.get("cleared") == 1 else "s"))
            elif kind == "scan_now":
                lines.append("- scan triggered")
            else:
                lines.append("- %s set to %s" % (kind.replace("set_", ""), action.get("value")))
        return "\n".join(lines)

    # ------------------------------------------------------------------ chat
    def policy_answer(self, question, snap):
        q = (question or "").lower()
        blocked = snap["blocked"]
        rules = snap["rules"]
        clients = snap["clients"]
        verdicts = snap["abuse_recent"]
        lines = []
        if any(word in q for word in ("block", "banned", "blacklist", "rule")):
            if rules:
                lines.append("Blacklist rules:")
                for rule in rules[:10]:
                    expiry = "permanent" if not rule.get("expires_at") else "expires in %ds" % max(0, rule["expires_at"] - snap["now"])
                    lines.append("- %s (%s)" % (rule["device"], expiry))
                if any(word in q for word in ("all", "every", "clear")):
                    lines.append("Say 'unblock all' to clear every rule, or 'unblock <device>' for one.")
            if blocked:
                lines.append("Currently blocked clients:")
                for name, state in list(blocked.items())[:10]:
                    lines.append("- %s: %s, %ds remaining" % (
                        name, state.get("category") or "blocked", state.get("remaining_s") or 0))
            if not rules and not blocked:
                lines.append("Nothing is blocked and there are no blacklist rules.")
        elif any(word in q for word in ("anomal", "problem", "wrong", "issue", "risk", "attack", "threat")):
            actions = self.policy(snap)
            if actions:
                lines.append("I found %d thing(s) that need action:" % len(actions))
                for action in actions:
                    lines.append("- %s %s: %s" % (
                        action["action"], action.get("target") or action.get("value"), action.get("reason")))
            else:
                lines.append("No unhandled anomalies: every block-worthy verdict is enforced, "
                             "rules are applied and enforcement is on.")
            hot = [c for c in clients if (c.get("total") or 0) > 0][:3]
            if hot:
                lines.append("Busiest devices:")
                for c in hot:
                    lines.append("- %s: %s requests, %s/min, %s" % (
                        c.get("client"), c.get("total"), c.get("rate_per_min"), c.get("label") or "no verdict"))
        elif any(word in q for word in ("client", "device", "top", "traffic", "who")):
            if clients:
                lines.append("Top devices right now:")
                for c in clients[:8]:
                    lines.append("- %s: %s requests, %s/min, %s" % (
                        c.get("client"), c.get("total"), c.get("rate_per_min"), c.get("label") or "no verdict"))
            else:
                lines.append("No devices have sent telemetry yet.")
        else:
            lines.append("State: abuse mode %s, enforcement %s, %d blocked, %d blacklist rule(s)." % (
                snap["abuse_mode"], "on" if snap["abuse_enforce"] else "off",
                len(blocked), len(rules)))
            risky = [v for v in verdicts if v.get("label") in ("block", "would-block")][:3]
            if risky:
                lines.append("Recent block-worthy verdicts:")
                for v in risky:
                    lines.append("- %s: %s (%.2f, %s)" % (
                        v.get("client"), v.get("label"), v.get("abuse_score") or 0,
                        v.get("category") or "unknown"))
            lines.append("The monitor would take %d action(s) on the next scan." % len(self.policy(snap)))
        return "\n".join(lines)

    # ------------------------------------------------------------------ chat
    CHAT_ACTIONS = (
        '{"action":"blacklist_device","device":"<ip or key>","ttl":<seconds, 0=permanent>,"reason":"..."}\n'
        '{"action":"unblacklist_device","device":"<ip or key>","reason":"..."}\n'
        '{"action":"unblacklist_all","reason":"..."}\n'
        '{"action":"block_client","target":"<client>","ttl":<60..86400>,"reason":"..."}\n'
        '{"action":"set_abuse_mode","value":"block"|"monitor"}\n'
        '{"action":"set_guard_mode","value":"block"|"monitor"}\n'
        '{"action":"set_abuse_enforce","value":true|false}\n'
        '{"action":"set_abuse_threshold","value":<0.01..1>}\n'
        '{"action":"set_guard_threshold","key":"prompt_injection"|"hidden_instructions"|"secret_request","value":<0.01..1>}\n'
        '{"action":"set_abuse_block_ttl","value":<5..86400>}\n'
        '{"action":"set_retention","value":<1..365>}\n'
        '{"action":"set_agent_mode","value":"act"|"monitor"}\n'
        '{"action":"set_agent_interval","value":<5..3600>}\n'
        '{"action":"set_agent_enabled","value":true|false}\n'
        '{"action":"scan_now"}'
    )

    def _validate_chat_actions(self, actions, snap):
        out = []
        for raw in actions[:MAX_ACTIONS]:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("action") or "")
            reason = str(raw.get("reason") or "chat command")

            def device_ok(value):
                return bool(re.fullmatch(r"[A-Za-z0-9_.:@-]{3,64}", str(value or "")))

            if kind in ("blacklist_device", "unblacklist_device"):
                device = str(raw.get("device") or "").strip()
                if not device_ok(device):
                    continue
                action: dict = {"action": kind, "device": device, "reason": reason}
                if kind == "blacklist_device":
                    try:
                        ttl = int(raw.get("ttl") or 0)
                    except (TypeError, ValueError):
                        ttl = 0
                    action["ttl"] = max(0, min(ttl, 30 * 86400))
                out.append(action)
            elif kind == "unblacklist_all":
                out.append({"action": kind, "reason": reason})
            elif kind == "block_client":
                target = str(raw.get("target") or "")
                known = {c.get("client") for c in snap["clients"]} | set(snap["blocked"])
                known |= {r["device"] for r in snap["rules"]}
                if target not in known or target in snap["blocked"]:
                    continue
                try:
                    ttl = int(raw.get("ttl") or 300)
                except (TypeError, ValueError):
                    ttl = 300
                out.append({"action": "block_client", "target": target,
                            "ttl": max(60, min(ttl, 86400)), "reason": reason})
            elif kind in ("set_abuse_mode", "set_guard_mode") and raw.get("value") in ("block", "monitor"):
                out.append({"action": kind, "value": raw["value"], "reason": reason})
            elif kind == "set_agent_mode" and raw.get("value") in ("act", "monitor"):
                out.append({"action": kind, "value": raw["value"], "reason": reason})
            elif kind in ("set_abuse_enforce", "set_agent_enabled") and isinstance(raw.get("value"), bool):
                out.append({"action": kind, "value": raw["value"], "reason": reason})
            elif kind == "set_guard_threshold" and raw.get("key") in (
                    "prompt_injection", "hidden_instructions", "secret_request"):
                try:
                    value = float(str(raw.get("value")))
                except (TypeError, ValueError):
                    continue
                out.append({"action": kind, "key": raw["key"],
                            "value": max(0.01, min(1.0, value)), "reason": reason})
            elif kind in ("set_abuse_threshold", "set_abuse_block_ttl",
                          "set_retention", "set_agent_interval"):
                try:
                    value = float(str(raw.get("value")))
                except (TypeError, ValueError):
                    continue
                out.append({"action": kind, "value": value, "reason": reason})
            elif kind == "scan_now":
                out.append({"action": kind, "reason": reason})
        return out

    def chat(self, message, history):
        message = str(message or "").strip()[:2000]
        if not message:
            return {"reply": "Ask me about blocked clients, blacklist rules, anomalies or top devices. "
                             "You can also tell me what to do: block an IP, change modes, thresholds, TTLs.",
                    "source": "policy", "error": ""}
        snap = self.snapshot()
        if self.runtime.upstream_key and time.time() >= self._llm_retry_at:
            try:
                result = self._chat_llm(snap, message, history)
                self.last_error = ""
                return self._chat_result(result, snap, message)
            except Exception as exc:
                self.last_error = "llm chat failed: %r" % exc
                self._llm_retry_at = time.time() + 300
        elif self.runtime.upstream_key and not self.last_error:
            self.last_error = "model chat paused after failures"
        return self._chat_fallback(message, snap)

    def _chat_result(self, result, snap, message):
        actions = self._validate_chat_actions(result.get("actions") or [], snap)
        reply = str(result.get("reply") or "").strip()
        if not actions:
            if not reply:
                return self._chat_fallback(message, snap)
            return {"reply": reply, "source": "llm", "error": ""}
        mode = self.runtime.agent_mode
        if mode == "act":
            self.execute(actions, "chat", mode)
            verified = self.verify_commands(actions, self.snapshot())
            if any(action["action"] == "scan_now" for action in actions):
                scan = self.run_once()
                verified += "\n- scan result: " + scan["summary"]
            self.last_summary = "chat: %d action(s)" % len(actions)
            body = (reply + "\n\n" if reply else "")
            return {"reply": body + "Applied:\n" + verified, "source": "llm", "error": ""}
        for action in actions:
            self.store.log_agent("monitor", "chat", action["action"],
                                 str(action.get("device") or action.get("target") or action.get("value")),
                                 "proposed")
        proposed = "\n".join("- %s %s" % (action["action"],
                                          action.get("device") or action.get("target") or action.get("value"))
                             for action in actions)
        body = (reply + "\n\n" if reply else "")
        return {"reply": body + "Agent is in monitor mode, so these are proposals only:\n" + proposed,
                "source": "llm", "error": ""}

    def _chat_fallback(self, message, snap):
        # model unavailable: deterministic commands still work, otherwise a policy answer
        commands, note = self.parse_commands(message) if message else ([], "")
        if commands:
            mode = self.runtime.agent_mode
            if mode == "act":
                self.execute(commands, "chat", mode)
                reply = "Done.\n" + self.verify_commands(commands, self.snapshot())
                if any(action["action"] == "scan_now" for action in commands):
                    scan = self.run_once()
                    reply += "\n- scan result: " + scan["summary"]
                self.last_summary = "chat: %d action(s)" % len(commands)
            else:
                reply = ("Agent is in monitor mode, so I only propose actions. Switch to act to apply:\n"
                         + "\n".join("- %s %s" % (action["action"],
                                                  action.get("device") or action.get("value"))
                                     for action in commands))
                for action in commands:
                    self.store.log_agent("monitor", "chat", action["action"],
                                         str(action.get("device") or action.get("value")), "proposed")
            if note:
                reply = note + "\n" + reply
            return {"reply": reply, "source": "command", "error": self.last_error}
        if not message:
            return {"reply": "Ask me about blocked clients, blacklist rules, anomalies or top devices.",
                    "source": "policy", "error": self.last_error}
        return {"reply": self.policy_answer(message, snap), "source": "policy", "error": self.last_error}
        if self.runtime.upstream_key and time.time() >= self._llm_retry_at:
            try:
                reply = self._chat_llm(snap, message, history)
                if reply:
                    self.last_error = ""
                    return {"reply": reply, "source": "llm", "error": ""}
            except Exception as exc:
                self.last_error = "llm chat failed: %r" % exc
                self._llm_retry_at = time.time() + 300
        elif self.runtime.upstream_key:
            self.last_error = "model chat paused after failures"
        return {"reply": self.policy_answer(message, snap), "source": "policy", "error": self.last_error}

    def _chat_llm(self, snap, message, history):
        compact = {
            "abuse_mode": snap["abuse_mode"],
            "abuse_enforce": snap["abuse_enforce"],
            "blocked": {k: v.get("remaining_s") for k, v in snap["blocked"].items()},
            "rules": [{"device": r["device"], "expires_at": r["expires_at"]} for r in snap["rules"][:20]],
            "clients": [
                {"client": c.get("client"), "requests": c.get("total"),
                 "rate_per_min": c.get("rate_per_min"), "label": c.get("label"),
                 "category": c.get("category")}
                for c in snap["clients"][:20]
            ],
            "verdicts": [
                {"client": v.get("client"), "label": v.get("label"), "score": v.get("abuse_score"),
                 "category": v.get("category"), "age_s": int(snap["now"] - v.get("ts", snap["now"]))}
                for v in snap["abuse_recent"][:20]
            ],
            "agent": {
                "runs": self.runs, "actions_taken": self.actions_taken,
                "last_error": self.last_error,
            },
        }
        messages = [{
            "role": "system",
            "content": (
                "You are the Aegis agent monitor, supervising an API guard that detects "
                "prompt injection and API abuse. You see a live snapshot and you can act. "
                "Return STRICT JSON: {\"reply\": \"<concise answer for the operator>\", "
                "\"actions\": [...]}.\n"
                "Only include actions when the operator asks for a change or the snapshot "
                "clearly needs intervention. Empty actions for plain questions. Max 5 actions.\n"
                "Allowed actions:\n" + self.CHAT_ACTIONS + "\n"
                "Rules: never block something already blocked; prefer blacklist_device for "
                "explicit block requests from the operator; use block_client only for clients "
                "already seen in the snapshot. Keep reply under 150 words, short bullet lines "
                "when listing. If the data does not answer the question, say so.\n\nSNAPSHOT:\n"
                + json.dumps(compact)
            ),
        }]
        for item in (history or [])[-10:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = str(item.get("content") or "")[:2000]
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                self.runtime.upstream_base + "/chat/completions",
                headers={
                    "Authorization": "Bearer " + self.runtime.upstream_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.runtime.upstream_model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 600,
                    "response_format": {"type": "json_object"},
                },
            )
        if response.status_code != 200:
            raise RuntimeError("http %s: %s" % (response.status_code, response.text[:120]))
        content = response.json()["choices"][0]["message"]["content"].strip()
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        return {"reply": content, "actions": []}

    # ------------------------------------------------------------------ run
    def run_once(self):
        snap = self.snapshot()
        source = "policy"
        actions = self.policy(snap)
        if self.runtime.agent_llm and time.time() >= self._llm_retry_at:
            reviewed = self.llm(snap)
            if reviewed is not None:
                source = "llm"
                actions = reviewed
            elif self.last_error:
                self._llm_retry_at = time.time() + 300
        elif self.runtime.agent_llm and not self.last_error:
            self.last_error = "model review paused after failures"
        mode = self.runtime.agent_mode
        applied = self.execute(actions, source, mode) if mode == "act" else []
        self.runs += 1
        self.last_run = time.time()
        if mode == "act":
            self.last_summary = "%d action%s applied" % (len(applied), "" if len(applied) == 1 else "s")
        else:
            self.last_summary = "%d action%s proposed (monitor)" % (len(actions), "" if len(actions) == 1 else "s")
            for action in actions:
                target = action.get("target") or action.get("value")
                self.store.log_agent(mode, source, action["action"], str(target), action.get("reason", ""), "proposed")
        return {
            "mode": mode,
            "source": source,
            "proposed": len(actions),
            "applied": len(applied),
            "actions": applied,
            "error": self.last_error,
            "summary": self.last_summary,
        }
