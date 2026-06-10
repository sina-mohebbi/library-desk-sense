"""Tests for the desk-recommendation scoring in analytics/recommend.py."""
import pandas as pd

import recommend


def test_slot_window_start_of_day():
    assert recommend._slot_window(0, 15) == ("00:00", "00:15")


def test_slot_window_mid_morning():
    # slot 36 * 15 min = 540 min = 09:00
    assert recommend._slot_window(36, 15) == ("09:00", "09:15")


def test_slot_window_wraps_past_midnight():
    # slot 95 * 15 = 1425 min = 23:45; the end (24:00) must wrap to 00:00
    assert recommend._slot_window(95, 15) == ("23:45", "00:00")


def _telemetry_two_slots():
    """25 days of samples at 09:00 (free, quiet, bright) and 14:00 (busy, loud, dark)."""
    days = pd.date_range("2026-01-01", periods=25, freq="D")

    def block(hour, occupied, noise, lux):
        idx = days + pd.Timedelta(hours=hour)
        return pd.DataFrame({"occupied": occupied, "noise": noise, "lux": lux}, index=idx)

    good = block(9, occupied=0, noise=10, lux=400)   # -> slot 36
    bad = block(14, occupied=1, noise=100, lux=10)   # -> slot 56
    return pd.concat([good, bad])


def test_score_slots_ranks_the_good_slot_first():
    ranked = recommend.score_slots(_telemetry_two_slots(), slot_minutes=15, min_samples=20)
    assert ranked.index[0] == 36                       # the free/quiet/bright morning slot wins
    assert ranked.iloc[0]["score"] > ranked.iloc[1]["score"]
    assert ranked.loc[36, "free"] == 1.0               # never occupied -> fully free


def test_score_slots_drops_slots_below_min_samples():
    # with min_samples above the 25 available per slot, nothing qualifies
    ranked = recommend.score_slots(_telemetry_two_slots(), slot_minutes=15, min_samples=1000)
    assert ranked.empty
