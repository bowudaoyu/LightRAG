#!/usr/bin/env python3
"""CLI entry point for testing the Museum Agent."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date

# Add project root to path so we can import lightrag
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from museum_agent.agent import MuseumAgent


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def main():
    setup_logging()
    logger = logging.getLogger("museum_agent.run")

    # Default test query
    user_message = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "我第一次来国博，有3小时，帮我规划一下怎么逛最值？"
    )
    today = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()

    logger.info("=" * 60)
    logger.info("Museum Agent Test Run")
    logger.info("Query: %s", user_message)
    logger.info("Date: %s", today)
    logger.info("=" * 60)

    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    agent = MuseumAgent(env_path=env_path)

    try:
        await agent.setup()
        result = await agent.run(user_message, date=today)

        # Print results
        print("\n" + "=" * 60)
        print("TOUR PLAN")
        print("=" * 60)
        print(result["plan_text"])

        print("\n" + "-" * 60)
        print("TIMINGS")
        print("-" * 60)
        for k, v in result["timings"].items():
            print(f"  {k}: {v:.2f}s")

        if result["validation_errors"]:
            print("\n" + "-" * 60)
            print("VALIDATION WARNINGS")
            print("-" * 60)
            for e in result["validation_errors"]:
                print(f"  ⚠ {e}")

        if result["plan"]:
            print("\n" + "-" * 60)
            print("PLAN STRUCTURE")
            print("-" * 60)
            plan = result["plan"]
            print(f"  Entrance: {plan.entrance_hint}")
            print(f"  Total duration: {plan.total_duration_min} min")
            print(f"  Stops: {len(plan.stops)}")
            for i, stop in enumerate(plan.stops):
                event_tag = f" [EVENT: {stop.anchor_event}]" if stop.anchor_event else ""
                print(f"    {i + 1}. {stop.arrive_time}-{stop.depart_time} {stop.zone_name} ({stop.duration_min}min){event_tag}")

    finally:
        await agent.teardown()


if __name__ == "__main__":
    asyncio.run(main())
