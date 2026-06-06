"""
================================================================
  common.py  --  shared settings for every Python component
================================================================
  One source of truth for the backend. Loads proxy/.env and
  exposes the InfluxDB / MQTT settings, the MQTT topic helper
  and a ready-made InfluxDB writer, so the proxy, the analytics
  module and the Telegram bot all read the same configuration.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

load_dotenv(Path(__file__).resolve().parent / ".env")


INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "library")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "desksense")
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))
COAP_PORT = int(os.getenv("COAP_PORT", "5683"))
COAP_BIND_HOST = os.getenv("COAP_BIND_HOST", "0.0.0.0")
DESK_ID = os.getenv("DESK_ID", "desk-01")


def topic(kind: str, desk: str | None = None) -> str:
    """Build the MQTT topic string for one desk."""
    return f"librarydesksense/{desk or DESK_ID}/{kind}"


def influx_client() -> InfluxDBClient:
    """Open a new InfluxDB client using the .env settings."""
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)


def make_writer():
    """Return the client and a small helper that writes one point."""
    client = influx_client()
    write_api = client.write_api(write_options=SYNCHRONOUS)

    def write(data_point: Point):
        """Write one point into the project bucket."""
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=data_point)

    return client, write
