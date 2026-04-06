"""Step 4: Code-based validation of the Plan JSON."""

from __future__ import annotations

import logging

from museum_agent.models import Plan, PlanStop

logger = logging.getLogger(__name__)

# Floor ordering for backtracking detection
FLOOR_ORDER = {
    "B1": 0,
    "F1": 1,
    "F2": 2,
    "F3": 3,
}

# Zone ID to floor mapping
ZONE_FLOOR: dict[str, str] = {
    "CN_NMC_ZONE_B1_NORTH": "B1",
    "CN_NMC_ZONE_B1_SOUTH": "B1",
    "CN_NMC_FAC_CAFETERIA": "B1",
    "CN_NMC_ZONE_F1_NORTH": "F1",
    "CN_NMC_ZONE_F1_SOUTH": "F1",
    "CN_NMC_FAC_VISITOR_CENTER": "F1",
    "CN_NMC_FAC_LUGGAGE": "F1",
    "CN_NMC_ZONE_F2_NORTH": "F2",
    "CN_NMC_ZONE_F2_SOUTH": "F2",
    "CN_NMC_FAC_GIFT_SHOP": "F2",
    "CN_NMC_ZONE_F3_NORTH": "F3",
    "CN_NMC_ZONE_F3_SOUTH": "F3",
    "CN_NMC_FAC_CAFE": "F3",
}

# Approximate travel time in minutes between floors
INTER_FLOOR_TRAVEL_MIN = 3
SAME_FLOOR_TRAVEL_MIN = 2


def _parse_hhmm(t: str) -> int:
    """Convert HH:MM to minutes since midnight."""
    parts = t.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _fmt_hhmm(minutes: int) -> str:
    """Convert minutes since midnight back to HH:MM."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _get_floor(zone_id: str) -> str | None:
    return ZONE_FLOOR.get(zone_id)


def _floor_distance(f1: str, f2: str) -> int:
    """Number of floors apart."""
    o1 = FLOOR_ORDER.get(f1, -1)
    o2 = FLOOR_ORDER.get(f2, -1)
    if o1 < 0 or o2 < 0:
        return 0
    return abs(o1 - o2)


def _estimate_travel(zone_a: str, zone_b: str) -> int:
    """Estimate travel time in minutes between two zones."""
    fa = _get_floor(zone_a)
    fb = _get_floor(zone_b)
    if fa is None or fb is None:
        return SAME_FLOOR_TRAVEL_MIN
    if fa == fb:
        return SAME_FLOOR_TRAVEL_MIN
    return INTER_FLOOR_TRAVEL_MIN * _floor_distance(fa, fb)


def validate_plan(plan: Plan, time_budget_min: int, start_time: str) -> list[str]:
    """Validate the plan and return a list of error messages. Empty list = valid."""
    errors: list[str] = []
    stops = plan.stops

    if not stops:
        errors.append("Plan has no stops")
        return errors

    start_minutes = _parse_hhmm(start_time)
    museum_close = _parse_hhmm("17:00")

    for i, stop in enumerate(stops):
        label = f"Stop {i + 1} ({stop.zone_name})"

        # Parse times
        try:
            arrive = _parse_hhmm(stop.arrive_time)
            depart = _parse_hhmm(stop.depart_time)
        except (ValueError, IndexError):
            errors.append(f"{label}: invalid time format (arrive={stop.arrive_time}, depart={stop.depart_time})")
            continue

        # Depart must be after arrive
        if depart <= arrive:
            errors.append(f"{label}: depart time {stop.depart_time} is not after arrive time {stop.arrive_time}")

        # Must not start before museum opens or user arrives
        if i == 0 and arrive < start_minutes:
            errors.append(f"{label}: arrive time {stop.arrive_time} is before start time {start_time}")

        # Must not exceed museum closing
        if depart > museum_close:
            errors.append(f"{label}: depart time {stop.depart_time} exceeds museum closing 17:00")

        # Timeline continuity: can we physically get from previous stop to this one?
        if i > 0:
            prev = stops[i - 1]
            try:
                prev_depart = _parse_hhmm(prev.depart_time)
            except (ValueError, IndexError):
                continue
            travel = _estimate_travel(prev.zone_id, stop.zone_id)
            earliest_arrive = prev_depart + travel
            if arrive < earliest_arrive:
                errors.append(
                    f"{label}: arrive {stop.arrive_time} is too early; "
                    f"previous stop departs {prev.depart_time}, "
                    f"travel ~{travel}min, earliest arrival {_fmt_hhmm(earliest_arrive)}"
                )

    # Backtracking detection: check if floor sequence has unnecessary reversals
    floors = []
    for stop in stops:
        f = _get_floor(stop.zone_id)
        if f and (not floors or floors[-1] != f):
            floors.append(f)

    if len(floors) >= 3:
        orders = [FLOOR_ORDER.get(f, -1) for f in floors]
        reversals = 0
        for i in range(1, len(orders)):
            if i >= 2:
                d_prev = orders[i - 1] - orders[i - 2]
                d_curr = orders[i] - orders[i - 1]
                if d_prev != 0 and d_curr != 0 and (d_prev > 0) != (d_curr > 0):
                    reversals += 1
        if reversals > 1:
            errors.append(
                f"Route has {reversals} direction reversals (floors: {' → '.join(floors)}); "
                "consider a more linear path to avoid backtracking"
            )

    # Total duration check (allow 10% overflow for flexibility)
    if plan.total_duration_min > time_budget_min * 1.10:
        errors.append(
            f"Total duration {plan.total_duration_min}min exceeds budget "
            f"{time_budget_min}min by more than 10%"
        )

    return errors
