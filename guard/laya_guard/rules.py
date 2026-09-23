"""Regex overrides for two spots the english checkpoint gets wrong.

- system prompt: it cannot tell "write me a system prompt" from "what is YOUR
  system prompt". extraction patterns block, benign authoring is let through.
- greetings: "Hello there deepseek" scores 0.95 because jailbreak corpora are
  full of "Hello <model>, you are now ...". a plain greeting is let through.

Anything asking for secrets or containing override language stays with Laya.
"""
import re

AUTHORING = re.compile(
    r"\b(write|draft|create|design|make|build|compose|improve|rewrite|update|"
    r"explain|template|example|guide|what makes)\b[^.!?]{0,40}\bsystem\s+(prompts?|messages?)",
    re.I,
)

EXTRACTION = re.compile(
    r"(\byour\s+(?:\w+\s+){0,2}(system\s+)?(prompts?|messages?|instructions?)\b"
    r"|\b(system|hidden)\s+(prompts?|messages?|instructions?)\b[\s\S]{0,20}?(verbatim|above|you were)"
    r"|\b(instructions|text|prompt|message)\s+above\b"
    r"|before this conversation"
    r"|you were (given|told)"
    r"|\b(initial|original)\s+instructions\b)",
    re.I,
)

MALICIOUS = re.compile(
    r"\b(ignore[sd]?|disregard|bypass|override|forget)\b[\s\S]{0,30}?\b(rules?|instructions?|policy|restrictions?)\b"
    r"|\bno restrictions?\b|jailbreak",
    re.I,
)

SECRETS = re.compile(r"\b(api[\s_-]*keys?|passwords?|secrets?|credentials?|tokens?)\b", re.I)

GREETING = re.compile(
    r"^\s*(hi+|hello+|hey+|yo|howdy|greetings|good\s+(morning|afternoon|evening))\b",
    re.I,
)

# words that turn a greeting into a manipulation attempt
MANIPULATION = re.compile(
    r"\b(ignore[sd]?|disregard|override|bypass|forget|unfiltered|jailbreak|pretend|roleplay|"
    r"act as|no (rules|filters|restrictions|limits)|without (rules|filters|restrictions|limits)|"
    r"developer mode|system prompt|system message|instructions?)\b",
    re.I,
)

RULE_REASONS = {
    "block:extraction": "rule: system-prompt extraction pattern",
    "allow:authoring": "rule: benign system-prompt authoring request",
    "allow:greeting": "rule: greeting without manipulation markers",
}


def rule_decision(text):
    """'block:...' / 'allow:...' when a rule fires, None to let Laya decide."""
    text = text or ""
    extraction = bool(EXTRACTION.search(text))
    authoring = bool(AUTHORING.search(text))
    # DAN is case sensitive on purpose, otherwise anyone named Dan trips it
    suspicious = bool(MALICIOUS.search(text) or SECRETS.search(text) or "DAN" in text)
    if extraction and not authoring:
        return "block:extraction"
    if authoring and not extraction and not suspicious:
        return "allow:authoring"
    if GREETING.match(text) and not (extraction or suspicious or MANIPULATION.search(text)):
        return "allow:greeting"
    return None
