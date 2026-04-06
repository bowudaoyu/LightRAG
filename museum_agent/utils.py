"""Shared utility functions for the museum agent."""

from __future__ import annotations

import json
import os
from datetime import date, datetime

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# Holiday date ranges where Monday closure is waived.
# Loaded once from museum.json; format: list of (start_date, end_date) tuples.
_HOLIDAY_RANGES: list[tuple[date, date]] | None = None


def _load_holiday_ranges() -> list[tuple[date, date]]:
    """Parse holidays_2026 from museum.json into date ranges."""
    global _HOLIDAY_RANGES
    if _HOLIDAY_RANGES is not None:
        return _HOLIDAY_RANGES

    _HOLIDAY_RANGES = []
    museum_json = os.path.join(os.path.dirname(__file__), "..", "museum.json")
    try:
        with open(museum_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        for h in data.get("holidays_2026", []):
            dates_str = h.get("dates", "")
            if "至" in dates_str:
                start_str, end_str = dates_str.split("至", 1)
                _HOLIDAY_RANGES.append((
                    date.fromisoformat(start_str.strip()),
                    date.fromisoformat(end_str.strip()),
                ))
    except Exception:
        pass
    return _HOLIDAY_RANGES


def is_museum_closed(date_str: str) -> tuple[bool, str]:
    """Check if the museum is closed on the given date.

    Returns:
        (is_closed, reason) — reason is empty string if open.
    """
    d = date.fromisoformat(date_str)
    weekday = WEEKDAY_NAMES[d.weekday()]

    # Only Monday is a regular closure day
    if d.weekday() != 0:  # 0 = Monday
        return False, ""

    # Check if this Monday falls within a holiday period
    for start, end in _load_holiday_ranges():
        if start <= d <= end:
            return False, ""

    return True, f"{date_str}（{weekday}）为国博每周例行闭馆日，不对外开放"


def compute_weekday(date_str: str) -> str:
    """Compute Chinese weekday name from YYYY-MM-DD string."""
    d = date.fromisoformat(date_str)
    return WEEKDAY_NAMES[d.weekday()]


def compute_current_time() -> str:
    """Return current time as HH:MM."""
    return datetime.now().strftime("%H:%M")


def compute_end_time(start_time: str, budget_min: int) -> str:
    """Compute end time from start + budget, capped at museum closing 17:00."""
    h, m = map(int, start_time.split(":"))
    total = h * 60 + m + budget_min
    museum_close = 17 * 60
    total = min(total, museum_close)
    return f"{total // 60:02d}:{total % 60:02d}"
