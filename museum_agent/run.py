#!/usr/bin/env python3
"""CLI entry point for testing the Museum Agent.

Usage examples:
  # Interactive mode (default) — prompts for input
  python -m museum_agent.run

  # Direct query
  python -m museum_agent.run -q "我第一次来国博，有3小时"

  # Specify date and streaming
  python -m museum_agent.run -q "带孩子和老人，有半天时间" --date 2026-04-05 --stream

  # All options
  python -m museum_agent.run -q "我对青铜器特别感兴趣" --date 2026-04-05 --stream --env .env
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date

# Add project root to path so we can import lightrag
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from museum_agent.agent import MuseumAgent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Museum AI Tour Guide Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s -q "我有3小时"
  %(prog)s -q "带孩子逛国博" --date 2026-04-05 --stream
  %(prog)s                          # interactive mode
""",
    )
    parser.add_argument(
        "-q", "--query",
        type=str,
        default=None,
        help="User query (if omitted, enters interactive mode)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=date.today().isoformat(),
        help="Visit date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Enable streaming output (plan text prints in real-time)",
    )
    parser.add_argument(
        "--env",
        type=str,
        default=None,
        help="Path to .env file (default: auto-detect)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def main():
    args = parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger("museum_agent.run")

    # Resolve env path
    env_path = args.env
    if env_path is None:
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")

    # Get query: from args or interactive input
    if args.query:
        user_message = args.query
    else:
        print("Museum AI Tour Guide Agent")
        print("-" * 40)
        user_message = input("Please describe your visit plan: ").strip()
        if not user_message:
            user_message = "我第一次来国博，有3小时，帮我规划一下怎么逛最值？"
            print(f"(using default: {user_message})")

    logger.info("=" * 60)
    logger.info("Museum Agent")
    logger.info("  Query: %s", user_message)
    logger.info("  Date: %s", args.date)
    logger.info("  Mode: %s", "streaming" if args.stream else "batch")
    logger.info("=" * 60)

    agent = MuseumAgent(env_path=env_path)

    try:
        await agent.setup()

        if args.stream:
            await run_streaming(agent, user_message, args.date)
        else:
            await run_batch(agent, user_message, args.date)
    finally:
        await agent.teardown()


async def run_streaming(agent: MuseumAgent, user_message: str, visit_date: str):
    """Run with streaming output — plan text prints in real-time."""
    print("\n" + "=" * 60, flush=True)
    plan_started = False

    async for event_type, data in agent.run_stream(user_message, visit_date):
        if event_type == "status":
            print(f"\r[status] {data}", flush=True)

        elif event_type == "chunk":
            if not plan_started:
                print("\n" + "=" * 60)
                print("TOUR PLAN (streaming)")
                print("=" * 60, flush=True)
                plan_started = True
            print(data, end="", flush=True)

        elif event_type == "plan_json":
            stops = data.get("stops", [])
            print("\n\n" + "-" * 60)
            print("PLAN STRUCTURE")
            print("-" * 60)
            print(f"  Entrance: {data.get('entrance_hint', '?')}")
            print(f"  Total duration: {data.get('total_duration_min', '?')} min")
            print(f"  Stops: {len(stops)}")
            for i, stop in enumerate(stops):
                event_tag = f" [EVENT: {stop.get('anchor_event')}]" if stop.get("anchor_event") else ""
                print(f"    {i + 1}. {stop.get('arrive_time', '?')}-{stop.get('depart_time', '?')} "
                      f"{stop.get('zone_name', '?')} ({stop.get('duration_min', '?')}min){event_tag}")

        elif event_type == "validation":
            errors = data
            if errors:
                print("\n" + "-" * 60)
                print("VALIDATION WARNINGS")
                print("-" * 60)
                for e in errors:
                    print(f"  ⚠ {e}")
            else:
                print("\n  ✓ Validation passed")

        elif event_type == "timings":
            print("\n" + "-" * 60)
            print("TIMINGS")
            print("-" * 60)
            for k, v in data.items():
                print(f"  {k}: {v:.2f}s")

    print(flush=True)


async def run_batch(agent: MuseumAgent, user_message: str, visit_date: str):
    """Run in batch mode — wait for full result then print."""
    result = await agent.run(user_message, date=visit_date)

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


if __name__ == "__main__":
    asyncio.run(main())
