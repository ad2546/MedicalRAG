"""Shared utilities used across agents and routers."""

import uuid

DISCLAIMER = "Not a medical diagnosis; consult a clinician before making any clinical decisions."


def is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(val)
        return True
    except (ValueError, AttributeError):
        return False
