# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations


def positive(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def natural(value: object) -> bool:
    """0 included: counts and limits where 0 means none or unlimited."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
