"""Groundedness: every $ and % figure in the narrative must exist in the evidence (within a tolerance)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from leakhunter.domain.models import AnalysisReport, GroundingReport, ProposalCorrection

TOLERANCE = 0.5

_DOLLAR = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)")
_PERCENT = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s?%")


def extract_figures(text: str) -> list[float]:
    figures = [float(m.replace(",", "")) for m in _DOLLAR.findall(text)]
    figures += [float(m.replace(",", "")) for m in _PERCENT.findall(text)]
    return figures


def collect_numbers(obj: Any) -> set[float]:
    """Every number anywhere in a nested evidence structure (absolute values, since text drops signs)."""
    found: set[float] = set()
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        found.add(abs(float(obj)))
    elif isinstance(obj, dict):
        for value in obj.values():
            found |= collect_numbers(value)
    elif isinstance(obj, (list, tuple, set)):
        for value in obj:
            found |= collect_numbers(value)
    return found


def narrative_texts(report: AnalysisReport) -> list[str]:
    texts = [report.summary]
    texts += [cause.explanation for cause in report.root_causes]
    texts += [action.rationale for action in report.proposed_actions]
    texts += list(report.caveats)
    return texts


def check_grounding(
    report: AnalysisReport,
    evidence_numbers: Iterable[float],
    corrections: list[ProposalCorrection] | None = None,
) -> GroundingReport:
    allowed = sorted(set(evidence_numbers))
    checked = 0
    unmatched: list[str] = []
    for text in narrative_texts(report):
        for figure in extract_figures(text):
            checked += 1
            if not any(abs(figure - value) <= TOLERANCE for value in allowed):
                unmatched.append(f"{figure:g} in: {text[:120]}")
    corrections = corrections or []
    return GroundingReport(
        figures_checked=checked,
        unmatched=unmatched,
        proposal_corrections=corrections,
        ok=not unmatched and not corrections,
    )
