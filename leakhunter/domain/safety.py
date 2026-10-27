"""Deterministic safety policy: the hard gate that runs before any tool call (defence layer 1)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from leakhunter.domain.models import ActionType

ACTION_ALLOWLIST: frozenset[ActionType] = frozenset(ActionType)

_DESTRUCTIVE = re.compile(
    r"\b(delet\w*|terminat\w*|destroy\w*|wip(?:e|es|ed|ing)|drop\w*|remov\w*|shut\s*down|"
    r"shutting\s+down|kill\w*|purg\w*|nuke\w*)\b",
    re.IGNORECASE,
)
_BULK = re.compile(r"(\b(all|every|everything|entire)\b|\*)", re.IGNORECASE)
_PRODUCTION = re.compile(r"\b(prod|production)\b", re.IGNORECASE)
_RESOURCE_ID = re.compile(r"\b(?:i|vol|nat|db|asg|s3)-[0-9a-z][0-9a-z-]*\b", re.IGNORECASE)


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    rule: str | None = None
    reason: str | None = None


class SafetyPolicy:
    def evaluate(self, question: str) -> SafetyDecision:
        verb = _DESTRUCTIVE.search(question)
        if not verb:
            return SafetyDecision(allowed=True)
        bulk = _BULK.search(question)
        if bulk:
            return SafetyDecision(
                allowed=False,
                rule="destructive_bulk_request",
                reason=(
                    f"Refused: '{verb.group(0)}' combined with '{bulk.group(0)}' asks for a bulk "
                    "destructive change. LeakHunter only proposes individual, evidence-backed actions "
                    "that a human approves."
                ),
            )
        if _PRODUCTION.search(question) and not _RESOURCE_ID.search(question):
            return SafetyDecision(
                allowed=False,
                rule="destructive_production_request",
                reason=(
                    f"Refused: '{verb.group(0)}' targets production without naming a specific resource. "
                    "Name the resource id; any change is still proposed for human approval."
                ),
            )
        return SafetyDecision(allowed=True)
