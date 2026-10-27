import json

import pytest

from leakhunter.agent.llm import LLMRequest, Message, ToolResultBlock
from leakhunter.agent.prompts import build_user_turn
from leakhunter.agent.scripted_client import ScriptedLLMClient, parse_period
from leakhunter.domain.models import AnalysisReport

TOOLS = [{"name": "get_cost_data"}, {"name": "get_resource_details"}]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Why did our AWS bill spike?", "current"),
        ("Why did our bill spike in September?", "2026-09"),
        ("what happened in sept", "2026-09"),
        ("And in December?", "2025-12"),
        ("Explain 2026-08", "2026-08"),
    ],
)
def test_parse_period(question, expected):
    assert parse_period(question, "2026-10") == expected


def request(question, *results):
    from datetime import datetime, timezone

    messages = [
        Message(role="user", text=build_user_turn(question, datetime(2026, 11, 2, tzinfo=timezone.utc), "2026-10"))
    ]
    for name, payload, is_error in results:
        messages.append(
            Message(
                role="user",
                tool_results=[
                    ToolResultBlock(tool_call_id="t", name=name, content=json.dumps(payload), is_error=is_error)
                ],
            )
        )
    return LLMRequest(system="s", messages=messages, tools=TOOLS, output_schema={})


COST = {
    "comparison": {
        "period": "2026-10",
        "previous_period": "2026-09",
        "current_total": 200.0,
        "previous_total": 100.0,
        "delta_abs": 100.0,
        "delta_pct": 100.0,
        "is_spike": True,
        "spike_threshold_pct": 10.0,
        "top_movers": [{"resource_id": "i-abc", "delta_abs": 100.0, "previous": 0.0}],
    },
    "data_quality": {"warnings": []},
}


async def test_first_turn_calls_get_cost_data_with_parsed_period():
    response = await ScriptedLLMClient().complete(request("Why did our bill spike in September?"))
    assert response.stop_reason == "tool_use"
    [call] = response.tool_calls
    assert call.name == "get_cost_data" and call.arguments == {"period": "2026-09", "compare_with_previous": True}


async def test_second_turn_inspects_question_ids_then_top_movers():
    response = await ScriptedLLMClient().complete(request("Is vol-123 needed?", ("get_cost_data", COST, False)))
    [call] = response.tool_calls
    assert call.name == "get_resource_details"
    assert call.arguments["resource_ids"] == ["vol-123", "i-abc"]


async def test_tool_error_degrades_to_report_without_actions():
    response = await ScriptedLLMClient().complete(
        request(
            "Why?",
            ("get_cost_data", COST, False),
            ("get_resource_details", {"error": {"kind": "timeout", "message": "timeout after 2 attempts"}}, True),
        )
    )
    assert response.stop_reason == "end_turn"
    report = AnalysisReport.model_validate_json(response.text)
    assert report.proposed_actions == []
    assert any("timeout after 2 attempts" in c for c in report.caveats)
    assert "$100.00" in report.summary and "+100.0%" in report.summary
