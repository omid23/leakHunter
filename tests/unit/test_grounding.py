from leakhunter.domain.grounding import check_grounding, collect_numbers, extract_figures
from leakhunter.domain.models import AnalysisReport, ProposalCorrection, ReportAction, RootCause


def report(summary: str, rationale: str = "Idle at 2.1% CPU") -> AnalysisReport:
    return AnalysisReport(
        summary=summary,
        root_causes=[RootCause(resource_id="i-1", explanation="+$571.39 vs September")],
        proposed_actions=[
            ReportAction(
                action="stop_instance",
                resource_id="i-1",
                estimated_monthly_saving=571.39,
                rationale=rationale,
            )
        ],
        caveats=[],
    )


def test_extract_figures_handles_commas_signs_and_percentages():
    assert extract_figures("from $818.13 to $1,543.27 (+$725.14, -3.2 %), 88.6%") == [
        818.13,
        1543.27,
        725.14,
        3.2,
        88.6,
    ]
    assert extract_figures("5 malformed rows") == []


def test_collect_numbers_is_recursive_and_absolute():
    assert collect_numbers({"a": [1, {"b": -2.5}], "c": True, "d": "7"}) == {1.0, 2.5}


def test_grounded_report_passes():
    evidence = {571.39, 2.1, 818.13, 1543.27}
    result = check_grounding(report("Bill went from $818.13 to $1,543.27"), evidence)
    assert result.ok and result.figures_checked == 4 and result.unmatched == []


def test_invented_figure_is_flagged():
    result = check_grounding(report("We could save $9,999.00"), {571.39, 2.1})
    assert not result.ok
    assert result.unmatched and result.unmatched[0].startswith("9999")


def test_tolerance_allows_rounded_narration():
    assert check_grounding(report("Up 88.6%", rationale="2%"), {88.63, 571.39, 2.1}).ok


def test_corrections_make_grounding_fail():
    correction = ProposalCorrection(
        resource_id="i-1", field="estimated_monthly_saving", claimed=900, corrected=571.39
    )
    assert not check_grounding(report("ok"), {571.39, 2.1}, [correction]).ok
