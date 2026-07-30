"""Writes a run summary to the GitHub Actions job summary (or stdout)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DATA = Path("docs/data/latest.json")


def main() -> int:
    if not DATA.exists():
        print("Build produced no output file.")
        return 0

    d = json.loads(DATA.read_text(encoding="utf-8"))
    s, m = d["stats"], d["market"]

    lines = [
        f"### Screener refreshed — {d['as_of_date']}",
        "",
        f"- **{s['opportunities']}** opportunities from **{s['setup_hits']}** setup hits",
        f"- **{s['liquid']}** liquid of **{s['universe']}** listed",
        f"- **{s['pairs']}** pair setups",
        f"- Market regime: **{m['regime']}** "
        f"({m['breadth_above_200dma']:.0%} of liquid names above their 200DMA)",
        f"- Build time: {s['build_seconds']}s",
        "",
        "| Theme | Long | Short |",
        "|---|---:|---:|",
    ]
    for t in d["themes"][:20]:
        lines.append(f"| {t['label']} | {t['long']} | {t['short']} |")

    text = "\n".join(lines) + "\n"
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
