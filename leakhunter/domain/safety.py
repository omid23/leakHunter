"""Deterministic safety policy: the hard gate that runs before any tool call (defence layer 1).

A request is refused when a destructive verb is used as an instruction (not as a description of what
happened to the bill) and it targets resources in bulk, or production without a specific resource id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from leakhunter.domain.identifiers import RESOURCE_ID
from leakhunter.domain.models import ActionType

ACTION_ALLOWLIST: frozenset[ActionType] = frozenset(ActionType)

# Imperative or progressive forms only: "deleted" and "removed" describe the past, they never instruct.
_DESTRUCTIVE = re.compile(
    r"\b(delete|deleting|terminate|terminating|destroy|destroying|wipe|wiping|drop|dropping|remove|removing|"
    r"shut\s*down|shutting\s+down|kill|killing|purge|purging|nuke|nuking)\b",
    re.IGNORECASE,
)
# "costs drop", "the bill is dropping": the verb describes a cost movement, not an instruction.
_DESCRIPTIVE_SUBJECT = re.compile(
    r"\b(costs?|bills?|spend(?:ing)?|usage|charges?|prices?|totals?|rates?|it|they|which|that)\W*$",
    re.IGNORECASE,
)
# A bulk quantifier within the three words after the verb: "delete all ...", "wipe the entire ...".
_BULK_AFTER_VERB = re.compile(r"(?:\W+\w+){0,3}\W+(?:(all|every|everything|entire)\b|(\*))", re.IGNORECASE)
_PRODUCTION = re.compile(r"\b(prod|production)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    rule: str | None = None
    reason: str | None = None


class SafetyPolicy:
    def evaluate(self, question: str) -> SafetyDecision:
        names_resource = RESOURCE_ID.search(question) is not None
        for verb in _DESTRUCTIVE.finditer(question):
            if _DESCRIPTIVE_SUBJECT.search(question[: verb.start()]):
                continue
            bulk = _BULK_AFTER_VERB.match(question[verb.end() :])
            if bulk:
                quantifier = bulk.group(1) or bulk.group(2)
                return SafetyDecision(
                    allowed=False,
                    rule="destructive_bulk_request",
                    reason=(
                        f"Refused: '{verb.group(0)}' combined with '{quantifier}' asks for a bulk destructive "
                        "change. LeakHunter only proposes individual, evidence-backed actions that a human approves."
                    ),
                )
            if _PRODUCTION.search(question) and not names_resource:
                return SafetyDecision(
                    allowed=False,
                    rule="destructive_production_request",
                    reason=(
                        f"Refused: '{verb.group(0)}' targets production without naming a specific resource. "
                        "Name the resource id; any change is still proposed for human approval."
                    ),
                )
        return SafetyDecision(allowed=True)
