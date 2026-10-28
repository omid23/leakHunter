"""Resource identifier grammar shared by the safety policy, the scripted client and the eval scorers."""

from __future__ import annotations

import re

RESOURCE_ID_PATTERN = r"\b(?:i|vol|nat|db|asg|s3)-[0-9a-z][0-9a-z-]*\b"
RESOURCE_ID = re.compile(RESOURCE_ID_PATTERN, re.IGNORECASE)


def find_resource_ids(text: str) -> list[str]:
    """Resource ids mentioned in free text, in order of appearance, without duplicates."""
    return list(dict.fromkeys(RESOURCE_ID.findall(text)))
