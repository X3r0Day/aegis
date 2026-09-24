#!/usr/bin/env python3
"""Render the Aegis accuracy figure from the live scenario results.

Reads demo-scripts/aegis_scenario_results.json (produced by
demo_api_scenarios.py) and writes docs/aegis-accuracy.png.

Layout: one 100% stacked bar per scenario (allowed vs blocked), counts inside
the segments, outcome and cutoff time in the row label. The right panel shows
the prompt-injection checks the same way. If no attack scenario completed, the
headline says so.

    .venv/bin/python demo-scripts/aegis_accuracy_chart.py
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "aegis_scenario_results.json"
OUTPUT = HERE.parent / "docs" / "aegis-accuracy.png"

BG = "#0f1013"
PANEL = "#171a1e"
TEXT = "#d8d8e4"
MUTED = "#9a9dab"
FAINT = "#70737f"
GRID = "#343b43"
GREEN = "#73bf69"
RED = "#f2495c"
ORANGE = "#ff9830"

for _dir in ("Inter", "SpaceGrotesk"):
    for _ttf in sorted((Path.home() / ".local/share/fonts" / _dir).glob("*.ttf")):
        font_manager.fontManager.addfont(str(_ttf))

plt.rcParams.update({
    "font.family": ["Inter", "DejaVu Sans"],
    "font.weight": "bold",
    "axes.titleweight": "bold",
    "axes.labelweight": "bold",
    "text.color": TEXT,
    "axes.labelcolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": TEXT,
    "axes.edgecolor": GRID,
})


def stacked_bar(ax, y, allowed, blocked, allowed_label, blocked_label, height=0.62):
    total = allowed + blocked
    if total <= 0:
        return
    allowed_pct = 100.0 * allowed / total
    blocked_pct = 100.0 * blocked / total
    ax.barh(y, allowed_pct, color=GREEN, height=height)
    if blocked_pct > 0:
        ax.barh(y, blocked_pct, left=allowed_pct, color=RED, height=height)
    if allowed_pct >= 14:
        ax.text(allowed_pct / 2, y, allowed_label, ha="center", va="center",
                fontsize=13, color="#0f1013", fontweight="bold")
    if blocked_pct >= 14:
        ax.text(allowed_pct + blocked_pct / 2, y, blocked_label, ha="center", va="center",
                fontsize=13, color="#0f1013", fontweight="bold")


def style_axis(ax, max_pct=100):
    ax.set_facecolor(PANEL)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(axis="x", color=GRID, linewidth=0.7, alpha=0.5)
    ax.set_axisbelow(True)
    ax.set_xlim(0, max_pct)
    ax.set_xticks([0, 20, 40, 60, 80, 100])
    ax.set_xticklabels(["0", "20", "40", "60", "80", "100%"])


def main() -> None:
    if not RESULTS.exists():
        raise SystemExit("missing %s, run demo-scripts/demo_api_scenarios.py first" % RESULTS)
    data = json.loads(RESULTS.read_text())
    scenarios = data["scenarios"]
    injection = data["injection"]

    attacks = [s for s in scenarios if s["kind"] == "attack"]
    allowed_runs = [s for s in attacks if s["outcome"] == "completed, allowed"]

    fig, (left, right) = plt.subplots(
        1, 2, figsize=(15.5, 7.4), gridspec_kw={"width_ratios": [1.35, 1]})
    fig.patch.set_facecolor(BG)
    fig.subplots_adjust(left=0.275, right=0.975, top=0.755, bottom=0.19, wspace=0.62)

    rows = list(range(len(scenarios)))
    labels = []
    for i, s in enumerate(scenarios):
        total = s["allowed"] + s["blocked_by_aegis"]
        if s["cutoff"]:
            note = "cut off at %.1fs" % s["cutoff"]["first_block_at_s"]
        elif s["outcome"].startswith("completed"):
            note = "completed"
        else:
            note = s["outcome"]
        labels.append("%s  (%d req)\n%s" % (s["name"], total, note))
        stacked_bar(left, i, s["allowed"], s["blocked_by_aegis"],
                    str(s["allowed"]), str(s["blocked_by_aegis"]))

    left.set_yticks(rows)
    left.set_yticklabels(labels, fontsize=13, linespacing=1.55)
    left.invert_yaxis()
    left.set_ylim(len(scenarios) - 0.45, -0.55)
    style_axis(left)
    left.set_xlabel("share of the scenario's requests", fontsize=13, labelpad=10)
    left.set_title("live attack and benign scenarios", fontsize=17, pad=16, color=TEXT)

    attack_total = injection["attacks"]
    benign_total = injection["benign"]
    stacked_bar(right, 0, 0, injection["attacks_blocked"],
                "", "%d blocked" % injection["attacks_blocked"])
    stacked_bar(right, 1, injection["benign_allowed"], len(injection["benign_flagged"]),
                "%d allowed" % injection["benign_allowed"],
                "%d flagged" % len(injection["benign_flagged"]))
    right.set_yticks([0, 1])
    right.set_yticklabels([
        "prompt-injection attacks (%d)\n%d/%d blocked" % (
            attack_total, injection["attacks_blocked"], attack_total),
        "benign prompts (%d)\n%d/%d allowed" % (
            benign_total, injection["benign_allowed"], benign_total),
    ], fontsize=13, linespacing=1.55)
    right.set_ylim(1.55, -0.55)
    style_axis(right)
    right.set_xlabel("share of the checks", fontsize=13, labelpad=10)
    right.set_title("prompt-injection guard", fontsize=17, pad=16, color=TEXT)

    if attacks and not allowed_runs:
        headline = "0 of %d attack scenarios succeeded. Every stream was stopped in flight." % len(attacks)
        headline_color = GREEN
    elif allowed_runs:
        headline = "%d of %d attack scenarios ran to completion: %s" % (
            len(allowed_runs), len(attacks), ", ".join(s["name"] for s in allowed_runs))
        headline_color = ORANGE
    else:
        headline = "no attack scenarios in the results"
        headline_color = MUTED

    fig.text(0.5, 0.945, "Aegis detection accuracy", ha="center", fontsize=23,
             color=TEXT, fontweight="bold")
    fig.text(0.5, 0.898, "measured live against the running guard", ha="center",
             fontsize=13.5, color=MUTED)
    fig.text(0.5, 0.842, headline, ha="center", fontsize=15.5, color=headline_color,
             fontweight="bold")

    fig.legend(handles=[
        Patch(facecolor=GREEN, label="allowed (reached the API)"),
        Patch(facecolor=RED, label="blocked by Aegis (429)"),
    ], loc="lower center", ncol=2, frameon=False, fontsize=13.5,
        bbox_to_anchor=(0.5, 0.045), labelcolor=TEXT)

    fig.text(0.012, 0.012,
             "generated %s against %s | results: demo-scripts/aegis_scenario_results.json" % (
                 data.get("generated_at", ""), data.get("base", "")),
             ha="left", fontsize=10, color=FAINT)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, dpi=200, facecolor=BG)
    print("saved " + str(OUTPUT))
    print(headline)


if __name__ == "__main__":
    main()
