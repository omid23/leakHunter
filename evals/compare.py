"""Compare two eval reports: ``python -m evals.compare <baseline.json> <latest.json>`` (drift detection)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def compare(baseline: dict[str, Any], latest: dict[str, Any]) -> list[str]:
    lines = [
        f"baseline: {baseline.get('model')} prompt {baseline.get('prompt_version')} ({baseline.get('generated_at')})",
        f"latest:   {latest.get('model')} prompt {latest.get('prompt_version')} ({latest.get('generated_at')})",
    ]
    for name, current in latest["criteria"].items():
        before = baseline.get("criteria", {}).get(name)
        delta = current["pass_rate"] - before["pass_rate"] if before else None
        marker = "  REGRESSION" if delta is not None and delta < 0 else ""
        shown = f"{delta:+.0%}" if delta is not None else "new"
        lines.append(f"{name:18} {current['pass_rate']:6.0%} ({shown}){marker}")
    return lines


def main(argv: list[str]) -> int:
    baseline_path, latest_path = Path(argv[0]), Path(argv[1])
    if not baseline_path.exists():
        print(f"no baseline at {baseline_path}; nothing to compare")
        return 0
    lines = compare(json.loads(baseline_path.read_text()), json.loads(latest_path.read_text()))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
