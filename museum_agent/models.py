"""Data models for the museum agent."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UserIntent:
    """Parsed user intent from natural language input."""

    time_budget_min: int = 180
    start_time: str = "09:00"
    date: str = ""
    audience: str = "adult_solo"  # adult_solo / couple / family / senior / student
    has_child: bool = False
    has_elderly: bool = False
    interests: list[str] = field(default_factory=list)
    raw_query: str = ""


@dataclass
class PlanStop:
    """A single stop in the tour plan."""

    zone_id: str = ""
    zone_name: str = ""
    arrive_time: str = ""
    depart_time: str = ""
    duration_min: int = 0
    anchor_event: str | None = None
    artifacts: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    stories: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """Complete tour plan."""

    stops: list[PlanStop] = field(default_factory=list)
    entrance_hint: str = ""
    total_duration_min: int = 0
    skipped_events: list[str] = field(default_factory=list)
    post_visit_tips: list[str] = field(default_factory=list)
