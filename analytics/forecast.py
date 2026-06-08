"""
================================================================
  forecast.py -- noise and light forecast
================================================================
  This file uses ARIMA for noise and persistence for light, then
  writes one forecast value and the MAE number.
"""
import argparse
import datetime as dt
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tools.sm_exceptions import ConvergenceWarning

ANALYTICS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYTICS_DIR))
sys.path.insert(0, str(ANALYTICS_DIR.parent / "proxy"))
from db import load_telemetry
from common import make_writer, DESK_ID, INFLUX_BUCKET, INFLUX_ORG
from influxdb_client import Point, WritePrecision

RESAMPLE = "1min"
HORIZON = 15
ARIMA_ORDER = (0, 1, 1)
PERSISTENCE_METRICS = {"lux"}
MIN_POINTS = 40
TEST_FRACTION = 0.2


def latest_continuous(series):
    """Keep the most recent run of the series with no gap longer than 2 minutes."""
    cleaned_series = series.astype(float).dropna()
    if cleaned_series.empty:
        return cleaned_series
    gap_after_point = cleaned_series.index.to_series().diff() > dt.timedelta(minutes=2)
    if gap_after_point.any():
        last_gap_time = gap_after_point[gap_after_point].index[-1]
        cleaned_series = cleaned_series.loc[last_gap_time:]
    return cleaned_series


def fit_arima(values):
    """Fit the fixed ARIMA order, silencing the optimiser's noisy warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", UserWarning)
        model = ARIMA(values, order=ARIMA_ORDER,
                      enforce_stationarity=False, enforce_invertibility=False)
        return model.fit()


def mae(actual, predicted):
    """Mean absolute error."""
    return float(np.mean(np.abs(actual - predicted)))


def forecast_signal(series, name):
    """Pick the model for this signal, score it on held-out data, then forecast the next window."""
    clean_series = latest_continuous(series)
    if len(clean_series) < MIN_POINTS:
        print(f"[{name}] only {len(clean_series)} min of clean data; need {MIN_POINTS}. Skipping.")
        return None

    values = clean_series.to_numpy()
    test_count = max(5, int(len(values) * TEST_FRACTION))
    train_values, test_values = values[:-test_count], values[-test_count:]

    persistence_prediction = values[-test_count - 1:-1]
    use_persistence = name in PERSISTENCE_METRICS
    if use_persistence:
        model_name = "persistence"
        model_prediction = persistence_prediction
    else:
        model_name = f"ARIMA{ARIMA_ORDER}"
        model_prediction = np.asarray(fit_arima(train_values).forecast(steps=test_count))

    naive_mae = mae(test_values, persistence_prediction)
    model_mae = mae(test_values, model_prediction)
    result = {
        "name": name,
        "model": model_name,
        "mae": model_mae,
        "naive_mae": naive_mae,
        "skill_mae": max(0.0, 1.0 - model_mae / naive_mae) if naive_mae > 0 else 0.0,
        "train_points": int(len(train_values)),
        "test_points": int(test_count),
        "last_time": clean_series.index[-1],
    }

    if use_persistence:
        future_values = np.repeat(values[-1], HORIZON)
    else:
        future_values = np.asarray(fit_arima(values).forecast(steps=HORIZON))
    future_values = np.maximum(future_values, 0.0)
    result["future"] = [float(value) for value in future_values]
    result["forecast_value"] = float(np.mean(future_values))

    print(f"[{name}] {result['model']}  train={result['train_points']} "
          f"test={result['test_points']}  MAE={result['mae']:.2f}  "
          f"(naive MAE={result['naive_mae']:.2f}, skill={100 * result['skill_mae']:.0f}%)")
    return result


def write_to_influx(result, metric):
    """Clear the old forecast and write one next-window value + metrics."""
    client, write = make_writer()
    delete_api = client.delete_api()
    delete_span = dict(
        start=dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc),
        stop=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1),
        bucket=INFLUX_BUCKET, org=INFLUX_ORG,
    )
    for measurement in ("forecast", "forecast_metrics"):
        delete_api.delete(
            predicate=f'_measurement="{measurement}" AND desk="{DESK_ID}" AND metric="{metric}"',
            **delete_span)

    forecast_time = result["last_time"] + dt.timedelta(minutes=HORIZON)
    write(Point("forecast").tag("desk", DESK_ID).tag("metric", metric)
          .field("value", result["forecast_value"])
          .field("horizon_minutes", HORIZON)
          .time(forecast_time.to_pydatetime(), WritePrecision.NS))

    write(Point("forecast_metrics").tag("desk", DESK_ID).tag("metric", metric)
          .tag("model", result["model"])
          .field("forecast_value", result["forecast_value"])
          .field("mae", result["mae"])
          .field("naive_mae", result["naive_mae"])
          .field("skill_mae", result["skill_mae"])
          .field("train_points", result["train_points"]).field("test_points", result["test_points"])
          .field("horizon_minutes", HORIZON)
          .time(dt.datetime.now(dt.timezone.utc), WritePrecision.NS))
    client.close()
    print(f"[{metric}] wrote 1 next-{HORIZON}-minute forecast value + metrics to InfluxDB")


def json_entry(result):
    """A short, report-friendly summary of one signal's result."""
    if result is None:
        return None
    return {
        "model": result["model"],
        "mae": round(result["mae"], 4),
        "naive_mae": round(result["naive_mae"], 4),
        "skill_mae": round(result["skill_mae"], 4),
        "train_points": result["train_points"],
        "test_points": result["test_points"],
        "horizon_minutes": HORIZON,
        "forecast_value": round(result["forecast_value"], 4),
        "last_time": str(result["last_time"]),
    }


def main():
    """Run the forecast script from the command line."""
    parser = argparse.ArgumentParser(description="Forecast noise and light with MAE.")
    parser.add_argument("--hours", type=int, default=168, help="Lookback window in hours.")
    parser.add_argument("--no-write", action="store_true",
                        help="Evaluate and print only; do not update InfluxDB.")
    parser.add_argument("--output", default="forecast_results.json")
    args = parser.parse_args()

    telemetry = load_telemetry(hours=args.hours)
    if telemetry.empty:
        print("No telemetry yet. Start the proxy + device and let data accumulate.")
        return
    print(f"Loaded {len(telemetry)} telemetry rows from {telemetry.index[0]} to {telemetry.index[-1]}")
    minutely_means = telemetry.resample(RESAMPLE).mean(numeric_only=True)

    summary = {}
    for metric in ("noise", "lux"):
        result = forecast_signal(minutely_means[metric], metric)
        if result and not args.no_write:
            write_to_influx(result, metric)
        summary[metric] = json_entry(result)

    Path(args.output).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"saved {args.output}")


if __name__ == "__main__":
    main()
