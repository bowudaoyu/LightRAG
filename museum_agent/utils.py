"""Shared utility functions for the museum agent."""

from __future__ import annotations

from datetime import date, datetime

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


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
