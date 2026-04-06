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

    logger.info("=" * 50)
    logger.info("Plan Validation")
    logger.info("=" * 50)
    logger.info("  Time budget: %d min, start: %s", time_budget_min, start_time)
    logger.info("  Total stops: %d, planned duration: %d min", len(stops), plan.total_duration_min)
    logger.info("  Entrance hint: %s", plan.entrance_hint)

    if not stops:
        errors.append("Plan has no stops")
        logger.warning("  FAIL: plan has no stops")
        return errors

    # Log plan structure
    logger.info("  Plan stops:")
    for i, stop in enumerate(stops):
        event_tag = f" [EVENT: {stop.anchor_event}]" if stop.anchor_event else ""
        floor = _get_floor(stop.zone_id) or "?"
        logger.info("    %d. %s-%s %s (%s, %dmin)%s",
                     i + 1, stop.arrive_time, stop.depart_time,
                     stop.zone_name, floor, stop.duration_min, event_tag)
        if stop.artifacts:
            logger.info("       artifacts: %s", ", ".join(stop.artifacts[:5]))
        if stop.notices:
            logger.info("       notices: %s", ", ".join(stop.notices[:3]))
        if stop.stories:
            logger.info("       stories: %s", ", ".join(stop.stories[:3]))

    start_minutes = _parse_hhmm(start_time)
    museum_close = _parse_hhmm("17:00")

    # Check each stop
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
            else:
                gap = arrive - earliest_arrive
                if gap > 0:
                    logger.info("  check [%s]: %dmin gap between stops (travel=%dmin)", label, gap, travel)

    # Backtracking detection
    floors = []
    for stop in stops:
        f = _get_floor(stop.zone_id)
        if f and (not floors or floors[-1] != f):
            floors.append(f)

    floor_path = " → ".join(floors)
    logger.info("  Floor path: %s", floor_path)

    if len(floors) >= 3:
        orders = [FLOOR_ORDER.get(f, -1) for f in floors]
        reversals = 0
        for i in range(1, len(orders)):
            if i >= 2:
                d_prev = orders[i - 1] - orders[i - 2]
                d_curr = orders[i] - orders[i - 1]
                if d_prev != 0 and d_curr != 0 and (d_prev > 0) != (d_curr > 0):
                    reversals += 1
                    logger.info("  check: direction reversal at %s→%s→%s",
                                floors[i - 2], floors[i - 1], floors[i])
        if reversals > 1:
            errors.append(
                f"Route has {reversals} direction reversals (floors: {floor_path}); "
                "consider a more linear path to avoid backtracking"
            )
        elif reversals == 1:
            logger.info("  check: 1 reversal detected (acceptable for anchor-based routing)")

    # Total duration check (max 15 minutes over budget)
    max_allowed = time_budget_min + 15
    if plan.total_duration_min > max_allowed:
        errors.append(
            f"Total duration {plan.total_duration_min}min exceeds budget "
            f"{time_budget_min}min by {plan.total_duration_min - time_budget_min}min "
            f"(max allowed: {max_allowed}min)"
        )

    # Check last stop doesn't exceed end time
    if stops:
        last_stop = stops[-1]
        try:
            last_depart = _parse_hhmm(last_stop.depart_time)
            budget_end = start_minutes + time_budget_min
            if last_depart > budget_end + 15:
                errors.append(
                    f"Last stop departs at {last_stop.depart_time}, "
                    f"which is more than 15min past the budget end time "
                    f"{_fmt_hhmm(budget_end)}"
                )
        except (ValueError, IndexError):
            pass

    # Summary
    if errors:
        logger.warning("  Validation FAILED with %d errors:", len(errors))
        for e in errors:
            logger.warning("    - %s", e)
    else:
        logger.info("  Validation PASSED (0 errors)")

    if plan.skipped_events:
        logger.info("  Skipped events: %s", ", ".join(plan.skipped_events))
    if plan.post_visit_tips:
        logger.info("  Post-visit tips: %s", ", ".join(plan.post_visit_tips[:3]))

    logger.info("=" * 50)
    return errors
