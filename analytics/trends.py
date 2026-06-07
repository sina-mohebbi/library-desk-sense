"""
================================================================
  trends.py -- simple desk history summary
================================================================
  This file calculates utilisation, session length, noise, light
  and event counts from the saved InfluxDB data. The busiest and
  quietest hour ignore hours with too few samples and break ties
  by sample count, so a short partial hour at the edge of the
  collection window cannot win by chance.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import load_events, load_sessions, load_telemetry


def hourly_profile(telemetry):
    """Mean occupancy, noise and lux for each hour of the day (0..23)."""
    with_hour = telemetry.assign(hour=telemetry.index.hour)
    return with_hour.groupby("hour").agg(
        occupancy=("occupied", "mean"),
        noise=("noise", "mean"),
        lux=("lux", "mean"),
        samples=("occupied", "size"),
    )


def main(hours=168, output="trends_summary.json", min_samples=20):
    """Compute the trends, print them, and save the JSON summary."""
    telemetry = load_telemetry(hours=hours)
    if telemetry.empty:
        print("No telemetry yet. Start the proxy + device and let data accumulate.")
        return

    sessions = load_sessions(hours=hours)
    events = load_events(hours=hours)
    profile = hourly_profile(telemetry)

    reliable_profile = profile[profile["samples"] >= min_samples]
    if reliable_profile.empty:
        busiest_hour, quietest_hour = None, None
    else:
        busiest_hour = int(
            reliable_profile.sort_values(["occupancy", "samples"], ascending=[False, False]).index[0])
        quietest_hour = int(
            reliable_profile.sort_values(["noise", "samples"], ascending=[True, False]).index[0])

    utilisation = 100.0 * telemetry["occupied"].mean()
    session_count = int(len(sessions))
    avg_session_seconds = float(sessions["duration_s"].mean()) if session_count else 0.0
    mean_noise = float(telemetry["noise"].mean())
    mean_lux = float(telemetry["lux"].mean())
    event_counts = ({name: int(count) for name, count in events["event"].value_counts().items()}
                    if not events.empty else {})

    print(f"LibraryDeskSense trends over the last {hours} h  "
          f"({len(telemetry)} samples, "
          f"{telemetry.index[0]:%Y-%m-%d %H:%M} -> {telemetry.index[-1]:%Y-%m-%d %H:%M})")
    print("-" * 66)
    print(f"Desk utilisation   : {utilisation:5.1f}% of samples occupied")
    print(f"Completed sessions : {session_count} (avg {avg_session_seconds:.0f} s)")
    print(f"Mean noise / light : {mean_noise:.0f} / {mean_lux:.0f} lux")
    if busiest_hour is None:
        print(f"Busiest hour       : n/a (no hour has >= {min_samples} samples)")
        print(f"Quietest hour      : n/a (no hour has >= {min_samples} samples)")
    else:
        print(f"Busiest hour       : {busiest_hour:02d}:00 "
              f"({profile.loc[busiest_hour, 'occupancy'] * 100:.0f}% occupied, "
              f"n={int(profile.loc[busiest_hour, 'samples'])})")
        print(f"Quietest hour      : {quietest_hour:02d}:00 "
              f"(noise {profile.loc[quietest_hour, 'noise']:.0f}, "
              f"n={int(profile.loc[quietest_hour, 'samples'])})")
    if event_counts:
        print("Events             : " +
              ", ".join(f"{name}={count}" for name, count in sorted(event_counts.items())))
    else:
        print("Events             : none recorded")

    print("\nHourly trend:")
    print(f"  {'hour':>5} {'occupied':>9} {'noise':>7} {'lux':>7} {'samples':>8}")
    for hour, hour_row in profile.iterrows():
        print(f"  {hour:02d}:00 {hour_row['occupancy'] * 100:8.0f}% {hour_row['noise']:7.0f} "
              f"{hour_row['lux']:7.0f} {int(hour_row['samples']):8d}")

    summary = {
        "window_hours": hours,
        "samples": int(len(telemetry)),
        "utilisation_percent": round(utilisation, 2),
        "completed_sessions": session_count,
        "avg_session_s": round(avg_session_seconds, 1),
        "mean_noise": round(mean_noise, 2),
        "mean_lux": round(mean_lux, 2),
        "min_samples_per_hour": min_samples,
        "busiest_hour": busiest_hour,
        "quietest_hour": quietest_hour,
        "event_counts": event_counts,
        "hourly_trend": [
            {
                "hour": int(hour),
                "occupancy_percent": round(hour_row["occupancy"] * 100, 1),
                "mean_noise": round(hour_row["noise"], 1),
                "mean_lux": round(hour_row["lux"], 1),
                "samples": int(hour_row["samples"]),
            }
            for hour, hour_row in profile.iterrows()
        ],
    }
    with open(output, "w", encoding="utf-8") as json_file:
        json.dump(summary, json_file, indent=2)
    print(f"\nsaved {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Utilisation, noise/light and event trends.")
    parser.add_argument("--hours", type=int, default=168, help="Lookback window in hours.")
    parser.add_argument("--min-samples", type=int, default=20,
                        help="An hour needs at least this many samples to be eligible "
                             "for busiest_hour/quietest_hour.")
    parser.add_argument("--output", default="trends_summary.json")
    args = parser.parse_args()
    main(hours=args.hours, output=args.output, min_samples=args.min_samples)
