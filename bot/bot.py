"""
================================================================
  bot.py -- Telegram helper for LibraryDeskSense
================================================================
  This file answers status/stat commands and sends alerts when
  the desk reports bad noise or light events.
"""
import json
import os
import sys
import threading
import time

import requests
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(BASE_DIR, "..", "proxy"))
sys.path.append(os.path.join(BASE_DIR, "..", "analytics"))
from common import influx_client, topic, INFLUX_ORG, INFLUX_BUCKET, MQTT_HOST, MQTT_PORT, DESK_ID

load_dotenv(os.path.join(BASE_DIR, ".env"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "FILL_IN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_message(chat_id, text):
    """Send a plain-text message to a chat."""
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=10)
    except requests.RequestException as error:
        print("send error", error)


def query_first_row(flux):
    """Run a query and return the first record's values as a dict, or None."""
    with influx_client() as client:
        for table in client.query_api().query(flux, org=INFLUX_ORG):
            for record in table.records:
                return record.values
    return None


def query_first_value(flux):
    """Run a query and return just the first value, or None."""
    first_row = query_first_row(flux)
    return first_row.get("_value") if first_row else None


def cmd_status():
    """/status: the desk's latest occupancy, noise and light."""
    latest = query_first_row(f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "telemetry" and r.desk == "{DESK_ID}")
  |> filter(fn: (r) => r._field == "occupied" or r._field == "noise" or
                       r._field == "lux" or r._field == "session_s")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 1)
''')
    if not latest:
        return "No recent data for this desk."
    state = "OCCUPIED" if int(latest.get("occupied", 0)) else "FREE"
    return (f"{DESK_ID}: {state}\n"
            f"Session: {int(latest.get('session_s', 0))} s\n"
            f"Noise: {float(latest.get('noise', 0)):.0f}\n"
            f"Light: {float(latest.get('lux', 0)):.0f} lux")


def cmd_stats():
    """/stats: the 24h averages for utilisation, noise and light."""
    base_query = (f'from(bucket: "{INFLUX_BUCKET}") |> range(start: -24h) '
                  f'|> filter(fn: (r) => r._measurement == "telemetry" and r.desk == "{DESK_ID}")')
    utilisation = query_first_value(
        base_query + ' |> filter(fn: (r) => r._field == "occupied") |> group() |> mean()')
    if utilisation is None:
        return "No data in the last 24h."
    mean_noise = query_first_value(
        base_query + ' |> filter(fn: (r) => r._field == "noise") |> group() |> mean()')
    mean_lux = query_first_value(
        base_query + ' |> filter(fn: (r) => r._field == "lux") |> group() |> mean()')
    return (f"Last 24h for {DESK_ID}:\n"
            f"Utilisation: {100 * utilisation:.0f}%\n"
            f"Avg noise: {mean_noise:.0f}\n"
            f"Avg light: {mean_lux:.0f} lux")


COMMANDS = {
    "/status": cmd_status,
    "/stats": cmd_stats,
    "/start": lambda: "LibraryDeskSense bot. Try /status or /stats.",
}


def poll_commands():
    """Long-poll Telegram and answer the known commands."""
    next_update_offset = None
    print("[bot] polling Telegram...")
    while True:
        try:
            response = requests.get(f"{TELEGRAM_API}/getUpdates",
                                    params={"timeout": 30, "offset": next_update_offset}, timeout=40)
            for update in response.json().get("result", []):
                next_update_offset = update["update_id"] + 1
                message = update.get("message", {})
                chat_id = message.get("chat", {}).get("id")
                text = (message.get("text") or "").strip().lower()
                handler = next((command_handler
                                for command, command_handler in COMMANDS.items()
                                if text.startswith(command)), None)
                if handler and chat_id is not None:
                    send_message(chat_id, handler())
        except Exception as error:
            print("poll error", error)
            time.sleep(3)


def mqtt_alerts():
    """Push high-noise / poor-lighting events from MQTT to the configured chat."""
    def on_connect(client, userdata, flags, reason_code, properties=None):
        """Subscribe to desk events after MQTT connects."""
        client.subscribe(topic("events"), qos=1)

    def on_message(client, userdata, message):
        """Forward important desk events to Telegram."""
        try:
            event = json.loads(message.payload.decode())
        except ValueError:
            return
        if event.get("event") in ("high_noise_event", "poor_lighting_event") and TELEGRAM_CHAT_ID:
            icon = "⚠️" if event["event"] == "high_noise_event" else "\U0001f505"
            send_message(TELEGRAM_CHAT_ID,
                         f"{icon} {event['event']} at {event['desk']} (value {event.get('value')})")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(MQTT_HOST, MQTT_PORT, 60)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "FILL_IN":
        raise SystemExit("Set TELEGRAM_TOKEN in bot/.env before starting the bot.")
    if not TELEGRAM_CHAT_ID:
        print("[bot] TELEGRAM_CHAT_ID is empty; commands work, but push alerts are disabled.")
    threading.Thread(target=mqtt_alerts, daemon=True).start()
    poll_commands()
