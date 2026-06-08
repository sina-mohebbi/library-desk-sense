"""
================================================================
  recommend.py -- best quiet desk times
================================================================
  This file scores every 15-minute time-of-day slot by free time,
  noise and light, then returns the best slots to use the desk.
  Slots that were almost never free can be dropped with a minimum
  availability floor.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import load_telemetry

GOOD_LUX = 200.0


def _slot_window(slot, slot_minutes):
    """Turn a slot index back into an 'HH:MM-HH:MM' label."""
    start_total_min = int(slot) * slot_minutes
    end_total_min = start_total_min + slot_minutes
    start = f"{(start_total_min // 60) % 24:02d}:{start_total_min % 60:02d}"
    end = f"{(end_total_min // 60) % 24:02d}:{end_total_min % 60:02d}"
    return start, end


def score_slots(telemetry, slot_minutes=15, min_samples=20, min_free=0.0):
    """Score every time-of-day slot on free time, quietness and light; best first."""
    minutes_of_day = telemetry.index.hour * 60 + telemetry.index.minute
    slot = (minutes_of_day // slot_minutes).astype(int)
    grouped = telemetry.assign(slot=slot).groupby("slot").agg(
        free=("occupied", lambda occupied: 1.0 - occupied.mean()),
        noise_p90=("noise", lambda noise: noise.quantile(0.90)),
        lux=("lux", "median"),
        samples=("occupied", "size"),
    )
    grouped = grouped[grouped["samples"] >= min_samples]
    grouped = grouped[grouped["free"] >= min_free]
    if grouped.empty:
        return grouped

    min_noise, max_noise = grouped["noise_p90"].min(), grouped["noise_p90"].max()
    grouped["quietness"] = 1.0 - (grouped["noise_p90"] - min_noise) / (max_noise - min_noise + 1e-9)
    grouped["lighting"] = np.clip(grouped["lux"] / GOOD_LUX, 0.0, 1.0)
    grouped["score"] = (0.5 * grouped["free"] +
                        0.3 * grouped["quietness"] +
                        0.2 * grouped["lighting"])
    return grouped.sort_values("score", ascending=False)


def recommend(hours=168, top_n=5, min_samples=20, slot_minutes=15, min_free=0.0):
    """Load history and return the best `top_n` time-of-day slots as a list of dicts."""
    telemetry = load_telemetry(hours=hours)
    if telemetry.empty:
        return []
    ranked_slots = score_slots(telemetry, slot_minutes=slot_minutes,
                               min_samples=min_samples, min_free=min_free)
    if ranked_slots.empty:
        return []
    results = []
    for slot, row in ranked_slots.head(top_n).iterrows():
        start, end = _slot_window(slot, slot_minutes)
        results.append({
            "slot": int(slot),
            "window": f"{start}-{end}",
            "score": round(float(row["score"]), 3),
            "free_percent": round(float(row["free"]) * 100, 1),
            "noise_p90": round(float(row["noise_p90"]), 1),
            "median_lux": round(float(row["lux"]), 1),
            "samples": int(row["samples"]),
        })
    return results


def main():
    """Run the recommendation script from the command line."""
    parser = argparse.ArgumentParser(description="Recommend the best quiet time slots to use the desk.")
    parser.add_argument("--hours", type=int, default=168, help="History window in hours.")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--slot-minutes", type=int, default=15,
                        help="Length of each time-of-day slot in minutes (e.g. 15, 30, 60).")
    parser.add_argument("--min-samples", type=int, default=20,
                        help="Ignore slots with fewer samples than this.")
    parser.add_argument("--min-free", type=float, default=0.0,
                        help="Ignore slots whose availability is below this fraction "
                             "(e.g. 0.1 to skip slots that were almost never free).")
    parser.add_argument("--output", default="quietness_recommendation.json")
    args = parser.parse_args()

    recommendations = recommend(hours=args.hours, top_n=args.top_n,
                                min_samples=args.min_samples, slot_minutes=args.slot_minutes,
                                min_free=args.min_free)
    if not recommendations:
        print("Not enough history yet for a recommendation.")
    else:
        print(f"Best {args.slot_minutes}-minute slots to use the desk (free + quiet + well-lit):")
        for slot in recommendations:
            print(f"  {slot['window']}  score={slot['score']:.2f}  free={slot['free_percent']:.0f}%  "
                  f"noise_p90={slot['noise_p90']:.0f}  lux={slot['median_lux']:.0f}  n={slot['samples']}")

    Path(args.output).write_text(json.dumps({
        "scoring": "0.5*availability + 0.3*quietness(noise_p90) + 0.2*lighting(up to 200 lux)",
        "history_hours": args.hours,
        "slot_minutes": args.slot_minutes,
        "min_samples_per_slot": args.min_samples,
        "min_free_fraction": args.min_free,
        "recommendations": recommendations,
    }, indent=2), encoding="utf-8")
    print(f"saved {args.output}")


if __name__ == "__main__":
    main()
