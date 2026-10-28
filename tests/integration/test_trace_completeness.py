"""The per-run trace is the execution history: every step listed in PLAN.md §15 must appear in it."""

import json
import logging

from leakhunter.diagnostics import export
from leakhunter.logging_setup import JsonFormatter, RunIdFilter, current_run_id


async def test_trace_covers_every_step_of_an_approved_run(service):
    run = await service.run_question("Why did our AWS bill spike?")
    run = await service.approve(run.id, ["act_1"], actor="operator")
    events = [(e.name, e.status) for e in run.trace]
    assert events[0] == ("safety_check", "allowed")
    assert ("transition", "running") in events
    assert ("llm_call", "tool_use") in events and ("llm_call", "end_turn") in events
    assert ("tool:get_cost_data", "ok") in events and ("tool:get_resource_details", "ok") in events
    assert ("validation", "ok") in events
    assert ("transition", "awaiting_approval") in events
    assert ("approval", "approved") in events and ("execution", "stopped (mock)") in events
    assert events[-1] == ("transition", "completed")
    llm = next(e for e in run.trace if e.name == "llm_call")
    assert {"model", "usage", "tool_calls"} <= set(llm.detail) and llm.duration_ms is not None
    steps = [e.step for e in run.trace]
    assert steps == sorted(steps)  # chronological and monotonic


def test_json_log_lines_carry_run_id(capsys):
    record = logging.LogRecord("leakhunter.test", logging.INFO, __file__, 1, "tool attempt", None, None)
    record.tool = "get_cost_data"
    token = current_run_id.set("run_abc")
    try:
        RunIdFilter().filter(record)
    finally:
        current_run_id.reset(token)
    line = json.loads(JsonFormatter().format(record))
    assert line["run_id"] == "run_abc" and line["tool"] == "get_cost_data" and line["msg"] == "tool attempt"


async def test_sample_traces_export(tmp_path):
    paths = await export(tmp_path)
    assert {p.stem for p in paths} == {
        "happy_path_approved",
        "stale_billing_failed",
        "tool_timeout_degraded",
        "unsafe_refused",
    }
    happy = json.loads((tmp_path / "happy_path_approved.json").read_text())
    assert happy["status"] == "completed" and happy["executions"]
