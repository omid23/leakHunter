"""Run the eval dataset: ``python -m evals --client scripted|anthropic --repeats N [--out DIR]``.

DIR defaults to reports/evals/local/<client> (git-ignored).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from evals.runner import run_evals
from leakhunter.agent.llm import LLMClient
from leakhunter.agent.scripted_client import ScriptedLLMClient
from leakhunter.config import Settings
from leakhunter.logging_setup import configure_logging


def eval_settings(client: str) -> Settings:
    if client == "scripted":
        return Settings(
            _env_file=None,
            llm_provider="scripted",
            tool_timeout_s=0.05,
            fault_timeout_sleep_s=0.2,
            tool_retry_backoff_s=0.0,
            log_level="ERROR",
        )
    return Settings(llm_provider="anthropic", tool_timeout_s=2.0, fault_timeout_sleep_s=4.0, log_level="ERROR")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    parser.add_argument("--client", choices=["scripted", "anthropic"], default="scripted")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument(
        "--out", type=Path, help="output directory (default: reports/evals/local/<client>, git-ignored)"
    )
    args = parser.parse_args(argv)

    settings = eval_settings(args.client)
    configure_logging(settings.log_level, settings.log_json)

    def factory() -> LLMClient:
        if args.client == "scripted":
            return ScriptedLLMClient()
        from leakhunter.agent.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(settings)

    out = args.out or Path("reports") / "evals" / "local" / args.client
    report = asyncio.run(
        run_evals(
            client=args.client,
            client_factory=factory,
            settings=settings,
            repeats=args.repeats,
            case_ids=args.cases.split(",") if args.cases else None,
            out_dir=out,
        )
    )
    for name, c in report["criteria"].items():
        print(f"{name:18} {c['passed']:>3}/{c['total']:<3} {c['pass_rate']:6.0%}  CI95 {c['ci95']}")
    print(f"report: {out / 'report.md'}")
    if report["harness_errors"]:
        print(f"{report['harness_errors']} harness error(s): see {out / 'errors.jsonl'}", file=sys.stderr)
    if report["breaches"]:
        print("THRESHOLD BREACH: " + "; ".join(report["breaches"]), file=sys.stderr)
        return 1
    return 1 if report["harness_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
