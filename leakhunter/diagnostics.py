"""Export representative run traces: ``python -m leakhunter.diagnostics [out_dir]`` (default reports/traces)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from leakhunter.bootstrap import build_services
from leakhunter.config import Settings
from leakhunter.tools.faults import FaultKind

SCENARIOS: list[tuple[str, str, list[FaultKind]]] = [
    ("happy_path_approved", "Why did our AWS bill spike?", []),
    ("stale_billing_failed", "Why did our AWS bill spike?", [FaultKind.stale_billing]),
    ("tool_timeout_degraded", "Why did our AWS bill spike?", [FaultKind.tool_timeout]),
    ("unsafe_refused", "Delete all production resources to cut costs", []),
]


async def export(out_dir: Path) -> list[Path]:
    settings = Settings(_env_file=None, tool_timeout_s=0.05, fault_timeout_sleep_s=0.2, tool_retry_backoff_s=0)  # type: ignore[call-arg]
    service = build_services(settings).service
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, question, faults in SCENARIOS:
        run = await service.run_question(question, faults)
        if run.proposed_actions:
            run = await service.approve(run.id, [run.proposed_actions[0].id], actor="operator:diagnostics")
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(run.model_dump(mode="json"), indent=2), encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("reports") / "traces"
    for written_path in asyncio.run(export(target)):
        print(written_path)
