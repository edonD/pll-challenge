"""
Regenerate results.md from results.tsv.
DO NOT MODIFY THIS FILE.

Creates a leaderboard with:
- Score progression chart (ASCII)
- Best result highlight
- Full experiment table
- Sub-score breakdown
"""

import os
import json
from datetime import datetime

TSV_FILE = "results.tsv"
JSONL_FILE = "results.jsonl"
MD_FILE = "results.md"

TARGET_SCORE = 0.80
SPECS = {
    "lock_time": {"target": "< 50 µs", "unit": "µs"},
    "ripple": {"target": "< 5 mV", "unit": "mV"},
    "phase_noise": {"target": "< -90 dBc/Hz", "unit": "dBc/Hz"},
    "ref_spur": {"target": "< -60 dBc", "unit": "dBc"},
    "power": {"target": "< 5 mW", "unit": "mW"},
    "stability": {"target": "Yes", "unit": ""},
}


def read_tsv():
    rows = []
    if not os.path.exists(TSV_FILE):
        return rows
    with open(TSV_FILE) as f:
        header = None
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if header is None:
                header = parts
                continue
            if len(parts) >= 6:
                rows.append({
                    "commit": parts[0],
                    "score": float(parts[1]) if parts[1] else 0.0,
                    "lock_us": parts[2],
                    "ripple_mv": parts[3],
                    "power_mw": parts[4] if len(parts) > 4 else "",
                    "status": parts[5] if len(parts) > 5 else "",
                    "description": parts[6] if len(parts) > 6 else "",
                })
    return rows


def read_jsonl():
    results = []
    if not os.path.exists(JSONL_FILE):
        return results
    with open(JSONL_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return results


def ascii_chart(scores, width=60, height=15):
    """Generate ASCII chart of score progression."""
    if not scores:
        return "No data yet."

    lines = []
    max_score = max(max(scores), TARGET_SCORE, 0.01)
    min_score = min(scores)

    # Header
    lines.append(f"Score Progression ({len(scores)} experiments)")
    lines.append(f"Target: {TARGET_SCORE} {'─' * 40}")
    lines.append("")

    for row in range(height, -1, -1):
        val = min_score + (max_score - min_score) * row / height
        label = f"{val:6.3f} │"
        bar = ""
        for i, s in enumerate(scores[-width:]):
            s_row = int((s - min_score) / (max_score - min_score) * height) if max_score > min_score else 0
            if s_row == row:
                bar += "●"
            elif row == int((TARGET_SCORE - min_score) / (max_score - min_score) * height) if max_score > min_score else 0:
                bar += "─"
            elif s_row > row:
                bar += "█"
            else:
                bar += " "
        lines.append(label + bar)

    lines.append("       └" + "─" * min(len(scores), width))
    lines.append("        " + "Experiment #")

    return "\n".join(lines)


def generate_md(rows, jsonl_results):
    lines = []

    # Title
    lines.append("# 🔬 PLL Autoresearch Results")
    lines.append("")
    lines.append(f"*Auto-generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("")

    # Target specs
    lines.append("## Target Specifications")
    lines.append("")
    lines.append("| Spec | Target | Weight |")
    lines.append("|------|--------|--------|")
    lines.append("| Lock time | < 50 µs | 25% |")
    lines.append("| Phase noise @ 1 MHz | < -90 dBc/Hz | 25% |")
    lines.append("| Control voltage ripple | < 5 mV | 20% |")
    lines.append("| Reference spur | < -60 dBc | 15% |")
    lines.append("| Power | < 5 mW | 10% |")
    lines.append("| Stability | No ringing | 5% |")
    lines.append("")
    lines.append(f"**Goal: score ≥ {TARGET_SCORE}**")
    lines.append("")

    if not rows:
        lines.append("*No experiments run yet.*")
        return "\n".join(lines)

    # Summary stats
    scores = [r["score"] for r in rows]
    best_score = max(scores)
    best_idx = scores.index(best_score)
    best_row = rows[best_idx]
    keeps = sum(1 for r in rows if r["status"] == "keep")
    discards = sum(1 for r in rows if r["status"] == "discard")
    crashes = sum(1 for r in rows if r["status"] == "crash")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Stat | Value |")
    lines.append(f"|------|-------|")
    lines.append(f"| Total experiments | **{len(rows)}** |")
    lines.append(f"| Best score | **{best_score:.6f}** |")
    lines.append(f"| Target score | {TARGET_SCORE} |")
    lines.append(f"| Progress | **{best_score/TARGET_SCORE*100:.1f}%** of target |")
    lines.append(f"| Improvements (keep) | {keeps} |")
    lines.append(f"| Regressions (discard) | {discards} |")
    lines.append(f"| Crashes | {crashes} |")
    lines.append(f"| Best experiment | #{best_idx + 1} — {best_row['description']} |")
    lines.append("")

    # Progress bar
    pct = min(100, best_score / TARGET_SCORE * 100)
    filled = int(pct / 2)
    bar = "█" * filled + "░" * (50 - filled)
    lines.append(f"### Progress to Target")
    lines.append(f"```")
    lines.append(f"[{bar}] {pct:.1f}%")
    lines.append(f" 0.0{'─' * 22}current{'─' * 21}{TARGET_SCORE}")
    lines.append(f"```")
    lines.append("")

    # ASCII chart
    lines.append("## Score Progression")
    lines.append("")
    lines.append("```")
    lines.append(ascii_chart(scores))
    lines.append("```")
    lines.append("")

    # Best result details
    lines.append("## Current Best")
    lines.append("")
    lines.append(f"**Experiment #{best_idx + 1}** | Commit: `{best_row['commit']}` | Score: **{best_score:.6f}**")
    lines.append("")
    lines.append(f"*{best_row['description']}*")
    lines.append("")

    # Full experiment table
    lines.append("## All Experiments")
    lines.append("")
    lines.append("| # | Commit | Score | Lock (µs) | Ripple (mV) | Power (mW) | Status | Description |")
    lines.append("|---|--------|-------|-----------|-------------|------------|--------|-------------|")
    for i, r in enumerate(rows):
        status_icon = {"keep": "✅", "discard": "❌", "crash": "💥"}.get(r["status"], "?")
        lines.append(
            f"| {i+1} | `{r['commit'][:7]}` | {r['score']:.4f} | {r['lock_us']} | "
            f"{r['ripple_mv']} | {r['power_mw']} | {status_icon} {r['status']} | {r['description']} |"
        )
    lines.append("")

    # Score history (compact)
    lines.append("## Score History")
    lines.append("")
    lines.append("```")
    best_so_far = 0
    for i, r in enumerate(rows):
        marker = ""
        if r["score"] > best_so_far:
            best_so_far = r["score"]
            marker = " ★ NEW BEST"
        lines.append(f"  #{i+1:3d}  {r['score']:.6f}  {r['status']:8s}  {r['description'][:50]}{marker}")
    lines.append("```")
    lines.append("")

    lines.append("---")
    lines.append("*Generated by update_results.py — do not edit manually*")

    return "\n".join(lines)


if __name__ == "__main__":
    rows = read_tsv()
    jsonl = read_jsonl()
    md = generate_md(rows, jsonl)
    with open(MD_FILE, "w") as f:
        f.write(md)
    print(f"Updated {MD_FILE} with {len(rows)} experiments.")
