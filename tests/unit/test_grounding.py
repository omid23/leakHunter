from leakhunter.domain.grounding import check_grounding, collect_figures, extract_figures
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


def test_collect_figures_keeps_money_and_percent_values_only():
    evidence = {
        "comparison": {
            "current_total": 1543.27,
            "top_movers": [{"delta_abs": -571.39, "delta_pct": None, "resource_id": "i-1"}],
            "by_service_current": {"AmazonEC2": 731.39},
            "is_spike": True,
        },
        "data_quality": {"total_rows": 217, "skipped_rows": 5, "age_hours": 27.0, "skipped_reasons": {"bad_cost": 1}},
        "details": [{"resource": {"size_gib": 2000, "instance_count": 3, "avg_cpu_7d_pct": 2.1}, "period_cost": 7.74}],
    }
    assert collect_figures(evidence) == {1543.27, 571.39, 731.39, 2.1, 7.74}


def test_counts_and_sizes_never_ground_dollar_amounts():
    evidence = {
        "data_quality": {"total_rows": 217},
        "resource": {"size_gib": 2000, "avg_cpu_7d_pct": 2.1},
        "comparison": {"current_total": 5.0, "delta_abs": 571.39},
    }
    result = check_grounding(report("Row count $217.00, volume $2,000.00, total $5.00"), collect_figures(evidence))
    assert [u.split(" ")[0] for u in result.unmatched] == ["217", "2000"]


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
    correction = ProposalCorrection(resource_id="i-1", field="estimated_monthly_saving", claimed=900, corrected=571.39)
    assert not check_grounding(report("ok"), {571.39, 2.1}, [correction]).ok
