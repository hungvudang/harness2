# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations


def blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def clip(value: object, limit: int, *, reason: str = "truncated") -> str:
    """Keeps the first `limit` chars and says how many were dropped, so a reader of the
    clipped text never mistakes it for the whole. NUL bytes are removed: they break
    downstream JSON and terminal output. A `limit` of 0 or less disables clipping."""
    if value is None:
        return ""
    text = str(value).replace("\x00", "")
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + omission(len(text) - limit, reason)


def omission(count: int, reason: str = "truncated") -> str:
    return f"…(+{count} chars — {reason})"
