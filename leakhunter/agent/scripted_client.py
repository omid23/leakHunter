"""Deterministic reference policy behind the same ``LLMClient`` port.

It follows the system prompt's workflow exactly, so CI is deterministic and secret-free, and it doubles as
the eval oracle: if this client does not score 100 %, the grader (not the model) is wrong.
"""

from __future__ import annotations

import json
import re
from typing import Any

from leakhunter.agent.llm import LLMRequest, LLMResponse, Message, ToolCall
from leakhunter.agent.prompts import parse_question
from leakhunter.domain.identifiers import find_resource_ids
from leakhunter.domain.models import AnalysisReport, ReportAction, RootCause
from leakhunter.tools.cost_tools import COST_DATA_TOOL, RESOURCE_DETAILS_TOOL

_MONTHS = {
    name: index
    for index, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_MONTH_RE = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b", re.IGNORECASE)
_ISO_PERIOD_RE = re.compile(r"\b(\d{4})-(0[1-9]|1[0-2])\b")
_CURRENT_PERIOD_RE = re.compile(r"^Current billing period: (\d{4}-\d{2})$", re.MULTILINE)

DRIVER_SHARE = 0.10  # a mover is a root cause when it explains >= 10 % of the increase


def usd(value: float) -> str:
    return f"${abs(value):,.2f}"


def parse_period(question: str, current_period: str) -> str:
    iso = _ISO_PERIOD_RE.search(question)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}"
    named = _MONTH_RE.search(question)
    if not named:
        return "current"
    month = _MONTHS[named.group(1).lower()]
    year, current_month = (int(part) for part in current_period.split("-"))
    if month > current_month:
        year -= 1
    return f"{year:04d}-{month:02d}"


class ScriptedLLMClient:
    name = "scripted"
    model = "scripted-reference-policy"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        first_user = next(m.text or "" for m in request.messages if m.role == "user")
        question = parse_question(first_user)
        match = _CURRENT_PERIOD_RE.search(first_user)
        current_period = match.group(1) if match else "current"
        results = _tool_results(request.messages)
        tools = {tool["name"] for tool in request.tools}

        if COST_DATA_TOOL not in results and COST_DATA_TOOL in tools:
            period = parse_period(question, current_period)
            return self._call(COST_DATA_TOOL, {"period": period, "compare_with_previous": True})

        cost, cost_error = results.get(COST_DATA_TOOL, (None, None))
        if cost is not None and RESOURCE_DETAILS_TOOL not in results and RESOURCE_DETAILS_TOOL in tools:
            ids = find_resource_ids(question) + [m["resource_id"] for m in cost["comparison"]["top_movers"]]
            ids = list(dict.fromkeys(ids))[:5]
            if ids:
                return self._call(RESOURCE_DETAILS_TOOL, {"resource_ids": ids, "period": cost["comparison"]["period"]})

        details, details_error = results.get(RESOURCE_DETAILS_TOOL, (None, None))
        report = build_report(question, cost, cost_error, details, details_error)
        return LLMResponse(stop_reason="end_turn", text=report.model_dump_json(), model=self.model)

    def _call(self, name: str, arguments: dict[str, Any]) -> LLMResponse:
        call = ToolCall(id=f"toolu_scripted_{self.calls}", name=name, arguments=arguments)
        return LLMResponse(stop_reason="tool_use", tool_calls=[call], model=self.model)


def _tool_results(messages: list[Message]) -> dict[str, tuple[dict[str, Any] | None, str | None]]:
    """Latest result per tool name: (data, None) on success or (None, error message) on failure."""
    results: dict[str, tuple[dict[str, Any] | None, str | None]] = {}
    for message in messages:
        for block in message.tool_results:
            payload = json.loads(block.content)
            if block.is_error:
                error = payload.get("error", {})
                results[block.name] = (None, error.get("message") or "unknown error")
            else:
                results[block.name] = (payload, None)
    return results


def build_report(
    question: str,
    cost: dict[str, Any] | None,
    cost_error: str | None,
    details: dict[str, Any] | None,
    details_error: str | None,
) -> AnalysisReport:
    if cost is None:
        return AnalysisReport(
            summary="I could not load the billing data, so I cannot explain the bill.",
            root_causes=[],
            proposed_actions=[],
            caveats=[f"Billing data unavailable: {cost_error}"],
        )

    c = cost["comparison"]
    period, prev_period = c["period"], c["previous_period"]
    detail_by_id = {d["resource"]["resource_id"]: d for d in (details or {}).get("details", [])}
    findings = [f for d in detail_by_id.values() for f in d["findings"]]
    actionable = [f for f in findings if f["recommended_action"] and not f["below_threshold"]]

    summary: list[str] = []
    drivers: list[dict[str, Any]] = []
    if c["previous_total"] is None:
        summary.append(
            f"Total AWS cost for {period} was {usd(c['current_total'])}; there is no previous export to compare."
        )
    elif c["is_spike"]:
        summary.append(
            f"Your AWS bill rose from {usd(c['previous_total'])} in {prev_period} to {usd(c['current_total'])} in "
            f"{period} (+{usd(c['delta_abs'])}, +{c['delta_pct']:.1f}%)."
        )
        increase = sum(m["delta_abs"] for m in c["top_movers"])
        drivers = [m for m in c["top_movers"] if m["delta_abs"] >= DRIVER_SHARE * increase]
        if drivers:
            names = " and ".join(f"{m['resource_id']} (+{usd(m['delta_abs'])})" for m in drivers)
            summary.append(f"The main drivers are {names}.")
    else:
        direction = "fell" if c["delta_abs"] < 0 else "rose"
        sign = "-" if c["delta_abs"] < 0 else "+"
        summary.append(
            f"There was no significant spike: the bill {direction} from {usd(c['previous_total'])} in {prev_period} "
            f"to {usd(c['current_total'])} in {period} ({sign}{usd(c['delta_abs'])}, "
            f"{sign}{abs(c['delta_pct']):.1f}%), "
            f"within the {c['spike_threshold_pct']:g}% spike threshold."
        )

    for rid in find_resource_ids(question):
        if rid in detail_by_id:
            summary.append(_describe_resource(detail_by_id[rid]))

    driver_ids = {m["resource_id"] for m in drivers}
    for finding in actionable:
        if finding["resource_id"] not in driver_ids and finding["resource_id"] not in question:
            summary.append(
                f"{finding['resource_id']} is not a driver of the change but is still waste "
                f"({usd(finding['estimated_monthly_saving'])} per month)."
            )
    if actionable:
        total = sum(f["estimated_monthly_saving"] for f in actionable)
        summary.append(
            f"Acting on the {len(actionable)} proposed action(s) would save an estimated {usd(total)} per month; "
            "nothing changes until a human approves."
        )
    elif details_error:
        summary.append("Resource details were unavailable, so no remediation is proposed.")
    else:
        summary.append("No remediation is proposed.")

    root_causes = [
        RootCause(resource_id=m["resource_id"], explanation=_explain_driver(m, detail_by_id.get(m["resource_id"]), c))
        for m in drivers
    ]
    proposals = [
        ReportAction(
            action=f["recommended_action"],
            resource_id=f["resource_id"],
            estimated_monthly_saving=f["estimated_monthly_saving"],
            rationale=_rationale(f),
        )
        for f in actionable
    ]

    caveats = list(cost["data_quality"]["warnings"])
    if details_error:
        caveats.append(
            f"Resource details unavailable: {details_error}. No remediation is proposed without resource evidence."
        )
    for missing in (details or {}).get("missing", []):
        caveats.append(f"{missing} is not in the resource inventory.")
    for f in findings:
        if f["below_threshold"]:
            caveats.append(
                f"{f['resource_id']} looks wasteful but cost only {usd(f['estimated_monthly_saving'])} in "
                f"{f['evidence']['period']}, below the {usd(f['evidence']['min_action_saving_usd'])} action threshold."
            )
    return AnalysisReport(
        summary=" ".join(summary), root_causes=root_causes, proposed_actions=proposals, caveats=caveats
    )


def _describe_resource(detail: dict[str, Any]) -> str:
    r = detail["resource"]
    text = f"{r['resource_id']} ({r['name']}) cost {usd(detail['period_cost'])} in {detail['period']}"
    signals = {f["signal"] for f in detail["findings"]}
    if "idle_instance" in signals:
        return text + f" at {r['avg_cpu_7d_pct']}% average CPU over 7 days, so it looks idle."
    if "unattached_volume" in signals:
        return text + " and is not attached to any instance."
    if r.get("avg_cpu_7d_pct") is not None:
        return text + f" with {r['avg_cpu_7d_pct']}% average CPU; it is doing real work."
    return text + "."


def _explain_driver(mover: dict[str, Any], detail: dict[str, Any] | None, c: dict[str, Any]) -> str:
    growth = f"+{usd(mover['delta_abs'])} versus {c['previous_period']}"
    if detail is None:
        return f"Cost grew {growth}; resource details were unavailable."
    r = detail["resource"]
    signals = {f["signal"] for f in detail["findings"]}
    if "idle_instance" in signals:
        new = "New " if not mover["previous"] else ""
        return (
            f"{new}{r.get('instance_type') or r['resource_type']} '{r['name']}' running since "
            f"{r['created_at'][:10]} at {r['avg_cpu_7d_pct']}% average CPU: idle capacity ({growth})."
        )
    if "unattached_volume" in signals:
        return f"Volume '{r['name']}' is unattached but still billed ({growth})."
    if r.get("avg_cpu_7d_pct") is not None:
        return (
            f"Legitimate growth: '{r['name']}' scaled out under real load ({r['avg_cpu_7d_pct']}% average CPU), "
            f"{growth}. No action proposed."
        )
    return f"Usage grew ({growth}); no waste signal found."


def _rationale(finding: dict[str, Any]) -> str:
    e = finding["evidence"]
    if finding["signal"] == "idle_instance":
        return (
            f"Idle: {e['avg_cpu_7d_pct']}% average CPU over 7 days (threshold {e['idle_cpu_threshold_pct']}%) "
            f"while costing {usd(e['period_cost'])} in {e['period']}."
        )
    since = f" since {e['detached_at'][:10]}" if e.get("detached_at") else ""
    return f"Unattached{since}; it cost {usd(e['period_cost'])} in {e['period']} without serving any instance."
