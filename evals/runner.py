"""Eval runner: cases x repeats through the app's real entry point (``AgentService``), then scored and reported.

Every (case, repeat) gets fresh services, so no state leaks between samples. Harness errors are recorded in
errors.jsonl and never scored as model failures; refusals are graded outcomes, not errors.
"""

from __future__ import annotations

import json
import math
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.scorers import SCORERS, EvalOutcome, Score
from evals.thresholds import breaches
from leakhunter.agent.llm import LLMClient
from leakhunter.agent.prompts import PROMPT_VERSION
from leakhunter.bootstrap import build_services
from leakhunter.config import Settings
from leakhunter.domain.models import RunStatus
from leakhunter.tools.faults import FaultKind

DATASET_PATH = Path(__file__).parent / "dataset.json"
PRICES_PER_MTOK = {"claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0)}

ClientFactory = Callable[[], LLMClient]


def load_cases(ids: list[str] | None = None) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"]
    if ids:
        missing = set(ids) - {c["id"] for c in cases}
        if missing:
            raise ValueError(f"unknown case ids: {sorted(missing)}")
        cases = [c for c in cases if c["id"] in ids]
    return cases


@dataclass
class Sample:
    case_id: str
    rep: int
    status: str  # ok | truncated | error
    scores: list[Score] = field(default_factory=list)
    run: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float = 0.0


async def run_case(case: dict[str, Any], rep: int, settings: Settings, client_factory: ClientFactory) -> Sample:
    started = time.perf_counter()
    try:
        service = build_services(settings, llm=client_factory()).service
        analysis = await service.run_question(case["question"], [FaultKind(f) for f in case["faults"]])
        final, approved = analysis, []
        decision = case.get("decision")
        if decision and analysis.status is RunStatus.awaiting_approval:
            if decision["type"] == "approve":
                wanted = {tuple(p) for p in decision["select"]}
                approved = [a.id for a in analysis.proposed_actions if (a.action.value, a.resource_id) in wanted]
                if approved:
                    final = await service.approve(analysis.id, approved, actor="eval")
            else:
                final = await service.reject(analysis.id, "eval rejection", actor="eval")
        outcome = EvalOutcome(analysis=analysis, final=final, approved_ids=approved)
    except Exception:  # harness / API error: recorded separately, never scored as a model failure
        return Sample(case["id"], rep, "error", error=traceback.format_exc(), duration_ms=_ms(started))
    truncated = analysis.error is not None and "max_tokens" in analysis.error.message
    scores = [SCORERS[criterion].score(case, outcome) for criterion in case["criteria"]]
    return Sample(
        case["id"],
        rep,
        "truncated" if truncated else "ok",
        scores=scores,
        run=final.model_dump(mode="json"),
        duration_ms=_ms(started),
    )


def wilson_interval(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = passed / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


def summarize(samples: list[Sample], client: str, model: str, repeats: int, settings: Settings) -> dict[str, Any]:
    per_criterion: dict[str, list[bool]] = {}
    for sample in samples:
        for score in sample.scores:
            per_criterion.setdefault(score.criterion, []).append(score.passed)
    criteria: dict[str, dict[str, Any]] = {}
    for name, results in sorted(per_criterion.items()):
        low, high = wilson_interval(sum(results), len(results))
        criteria[name] = {
            "passed": sum(results),
            "total": len(results),
            "pass_rate": sum(results) / len(results),
            "ci95": [low, high],
        }

    usage: dict[str, int] = {}
    llm_latencies: list[float] = []
    for sample in samples:
        for key, value in (sample.run or {}).get("usage", {}).items():
            usage[key] = usage.get(key, 0) + value
        llm_latencies += [
            e["duration_ms"]
            for e in (sample.run or {}).get("trace", [])
            if e["name"] == "llm_call" and e["duration_ms"]
        ]
    price_in, price_out = PRICES_PER_MTOK.get(model, (0.0, 0.0))
    cost = (usage.get("input_tokens", 0) * price_in + usage.get("output_tokens", 0) * price_out) / 1_000_000
    pass_rates: dict[str, float] = {name: float(c["pass_rate"]) for name, c in criteria.items()}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client": client,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "repeats": repeats,
        "config": {
            "effort": settings.effort,
            "max_steps": settings.max_steps,
            "tool_timeout_s": settings.tool_timeout_s,
            "tool_max_attempts": settings.tool_max_attempts,
        },
        "samples": len(samples),
        "harness_errors": sum(s.status == "error" for s in samples),
        "criteria": criteria,
        "cases": _case_table(samples),
        "usage": usage,
        "estimated_cost_usd": round(cost, 4),
        "llm_latency_ms": {
            "mean": round(sum(llm_latencies) / len(llm_latencies), 2) if llm_latencies else None,
            "max": max(llm_latencies) if llm_latencies else None,
        },
        "breaches": breaches(client, pass_rates),
    }


def _case_table(samples: list[Sample]) -> dict[str, Any]:
    table: dict[str, Any] = {}
    for sample in samples:
        row = table.setdefault(sample.case_id, {"samples": 0, "errors": 0, "criteria": {}, "failures": []})
        row["samples"] += 1
        row["errors"] += sample.status == "error"
        for score in sample.scores:
            stats = row["criteria"].setdefault(score.criterion, {"passed": 0, "total": 0})
            stats["passed"] += score.passed
            stats["total"] += 1
            row["failures"] += [f"[rep {sample.rep}] {score.criterion}: {f}" for f in score.failures]
    return table


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# LeakHunter eval report — {report['client']}",
        "",
        f"- Model: `{report['model']}` · prompt version `{report['prompt_version']}` · repeats {report['repeats']}",
        f"- Samples: {report['samples']} · harness errors: {report['harness_errors']}",
        f"- Tokens: {report['usage'] or 'n/a'} · estimated cost ${report['estimated_cost_usd']:.4f}",
        f"- Generated: {report['generated_at']}",
        "",
        "## Pass rate per criterion",
        "",
        "| Criterion | Passed | Pass rate | 95% CI |",
        "|---|---|---|---|",
    ]
    for name, c in report["criteria"].items():
        lines.append(
            f"| {name} | {c['passed']}/{c['total']} | {c['pass_rate']:.0%} | {c['ci95'][0]:.2f}–{c['ci95'][1]:.2f} |"
        )
    lines += ["", "## Cases", "", "| Case | Criteria | Errors |", "|---|---|---|"]
    for case_id, row in report["cases"].items():
        crit = ", ".join(f"{k} {v['passed']}/{v['total']}" for k, v in row["criteria"].items())
        lines.append(f"| {case_id} | {crit} | {row['errors']} |")
    failures = [f for row in report["cases"].values() for f in row["failures"]]
    lines += ["", "## Failures", ""] + ([f"- {f}" for f in failures] or ["None."])
    lines += ["", "## Threshold breaches", ""] + ([f"- {b}" for b in report["breaches"]] or ["None — gate passed."])
    return "\n".join(lines) + "\n"


async def run_evals(
    *,
    client: str,
    client_factory: ClientFactory,
    settings: Settings,
    repeats: int = 1,
    case_ids: list[str] | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    cases = load_cases(case_ids)
    samples = [await run_case(case, rep, settings, client_factory) for case in cases for rep in range(1, repeats + 1)]
    report = summarize(samples, client, client_factory().model, repeats, settings)
    if out_dir is not None:
        write_outputs(out_dir, samples, report)
    return report


def write_outputs(out_dir: Path, samples: list[Sample], report: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    trajectories = out_dir / "trajectories"
    trajectories.mkdir(exist_ok=True)
    with (
        (out_dir / "results.jsonl").open("w", encoding="utf-8") as results,
        (out_dir / "errors.jsonl").open("w", encoding="utf-8") as errors,
    ):
        for s in samples:
            if s.status == "error":
                errors.write(json.dumps({"case_id": s.case_id, "rep": s.rep, "error": s.error}) + "\n")
                continue
            run = s.run or {}
            llm_calls = [e for e in run.get("trace", []) if e["name"] == "llm_call"]
            results.write(
                json.dumps(
                    {
                        "case_id": s.case_id,
                        "rep": s.rep,
                        "status": s.status,
                        "run_status": run.get("status"),
                        "stop_reason": llm_calls[-1]["status"] if llm_calls else None,
                        "usage": run.get("usage"),
                        "llm_latency_ms": [e["duration_ms"] for e in llm_calls],
                        "duration_ms": s.duration_ms,
                        "scores": {sc.criterion: {"passed": sc.passed, "failures": sc.failures} for sc in s.scores},
                    }
                )
                + "\n"
            )
            (trajectories / f"{s.case_id}__{s.rep}.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
