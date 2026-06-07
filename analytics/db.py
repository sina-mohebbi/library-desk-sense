"""
================================================================
  db.py  --  data access for the analytics module
================================================================
  Every analytics script reads its data through this file, so
  all the Flux queries live in one place. The measurement, field
  and tag names below match exactly what the proxy writes and the
  firmware expects -- do not rename them.
"""
import os
import sys

import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "proxy"))
from common import influx_client, INFLUX_BUCKET, INFLUX_ORG, DESK_ID


def _query(flux: str) -> pd.DataFrame:
    """Run one Flux query and return one DataFrame."""
    with influx_client() as client:
        result = client.query_api().query_data_frame(flux, org=INFLUX_ORG)
    if isinstance(result, list):
        result = pd.concat(result, ignore_index=True) if result else pd.DataFrame()
    return result


def load_telemetry(hours: int = 168, desk: str = DESK_ID) -> pd.DataFrame:
    """Load telemetry rows with occupied, noise, lux and session_s."""
    flux = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{hours}h)
      |> filter(fn: (r) => r._measurement == "telemetry" and r.desk == "{desk}")
      |> filter(fn: (r) => r._field == "occupied" or r._field == "noise" or
                           r._field == "lux" or r._field == "session_s")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> keep(columns: ["_time", "occupied", "noise", "lux", "session_s"])
    '''
    telemetry_df = _query(flux)
    if telemetry_df.empty:
        return telemetry_df
    telemetry_df = telemetry_df.rename(columns={"_time": "time"}).set_index("time").sort_index()
    return telemetry_df[["occupied", "noise", "lux", "session_s"]].astype(float)


def load_sessions(hours: int = 168, desk: str = DESK_ID) -> pd.DataFrame:
    """Load completed occupancy sessions and their duration."""
    flux = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{hours}h)
      |> filter(fn: (r) => r._measurement == "occupancy_sessions" and r.desk == "{desk}")
      |> filter(fn: (r) => r._field == "duration_s")
      |> keep(columns: ["_time", "_value"])
    '''
    sessions_df = _query(flux)
    if sessions_df.empty:
        return sessions_df
    return (sessions_df.rename(columns={"_time": "time", "_value": "duration_s"})
              .set_index("time").sort_index()[["duration_s"]].astype(float))


def load_events(hours: int = 168, desk: str = DESK_ID) -> pd.DataFrame:
    """Load the desk events saved by the proxy."""
    flux = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{hours}h)
      |> filter(fn: (r) => r._measurement == "events" and r.desk == "{desk}" and r._field == "value")
      |> keep(columns: ["_time", "event", "_value"])
    '''
    events_df = _query(flux)
    if events_df.empty:
        return events_df
    return (events_df.rename(columns={"_time": "time", "_value": "value"})
              .set_index("time").sort_index()[["event", "value"]])
