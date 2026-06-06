"""
================================================================
  proxy.py -- bridge from the desk to the database
================================================================
  This file receives HTTP, CoAP and MQTT data, then saves it in
  InfluxDB for Grafana, analytics and the bot.
"""
import json
import threading
import asyncio
import datetime as dt

import paho.mqtt.client as mqtt
from flask import Flask, request
import aiocoap
import aiocoap.resource as resource
from influxdb_client import Point, WritePrecision

from common import (make_writer, topic, DESK_ID, MQTT_HOST, MQTT_PORT,
                    HTTP_PORT, COAP_PORT, COAP_BIND_HOST)

_influx_client, write_point = make_writer()
print("[proxy] InfluxDB writer ready")


def utc_now():
    """One server timestamp (UTC) that every point gets stamped with."""
    return dt.datetime.now(dt.timezone.utc)


def store_telemetry(sample: dict, transport: str):
    """Save one telemetry sample to InfluxDB. `sample` is the json the ESP32 sent."""
    desk = sample.get("desk", DESK_ID)
    session_seconds = sample.get("session_s", 0)
    noise = sample.get("noise", 0)
    lux = sample.get("lux", 0.0)

    point = (Point("telemetry")
             .tag("desk", desk)
             .tag("transport", transport)
             .field("occupied", 1 if sample.get("occupied") else 0)
             .field("session_s", int(session_seconds))
             .field("noise", float(noise))
             .field("lux", float(lux))
             .time(utc_now(), WritePrecision.NS))
    write_point(point)
    print(f"[{transport}] telemetry {sample}")


def store_event(event_data: dict):
    """Save one desk event. When the desk is released we also write the session length."""
    event_name = event_data.get("event", "unknown")
    value = float(event_data.get("value", 0.0))
    desk = event_data.get("desk", DESK_ID)
    received_at = utc_now()

    point = (Point("events")
             .tag("desk", desk)
             .tag("event", event_name)
             .field("value", value)
             .time(received_at, WritePrecision.NS))
    write_point(point)

    if event_name == "desk_released" and value >= 0:
        session_point = (Point("occupancy_sessions")
                         .tag("desk", desk)
                         .field("duration_s", value)
                         .time(received_at, WritePrecision.NS))
        write_point(session_point)
    print(f"[mqtt] event {event_data}")


def store_config_change(config_data: dict):
    """Save an applied config change, so there's a record of what was set and when."""
    point = Point("config_changes").tag("desk", config_data.get("desk", DESK_ID))
    for key, value in config_data.items():
        if key == "desk":
            continue
        point = point.field(key, value if isinstance(value, (int, float)) else str(value))
    point = point.time(utc_now(), WritePrecision.NS)
    write_point(point)
    print(f"[mqtt] config change {config_data}")


def publish_config(config_data: dict):
    """Push a config change onto the desk's MQTT config topic. Returns the topic."""
    desk = config_data.get("desk", DESK_ID)
    config_topic = topic("config", desk)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()
    try:
        publish_info = client.publish(config_topic, json.dumps(config_data), qos=1)
        publish_info.wait_for_publish(timeout=10)
        if not publish_info.is_published():
            raise TimeoutError("MQTT broker did not acknowledge the config message")
    finally:
        client.disconnect()
        client.loop_stop()
    print(f"[http] config published {config_data} -> {config_topic}")
    return config_topic


flask_app = Flask(__name__)


@flask_app.post("/telemetry")
def http_telemetry():
    """HTTP telemetry endpoint the ESP32 posts to (HTTP or AUTO mode)."""
    try:
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        store_telemetry(payload, "http")
        return ("", 204)
    except (TypeError, ValueError, KeyError) as error:
        return ({"error": str(error)}, 400)


@flask_app.post("/config")
def http_config():
    """HTTP endpoint the config tool calls to change settings at runtime."""
    try:
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        if "desk" not in payload:
            payload["desk"] = DESK_ID
        config_topic = publish_config(payload)
        return {"published": True, "topic": config_topic, "config": payload}
    except (TypeError, ValueError, TimeoutError, OSError) as error:
        return {"error": str(error)}, 400


@flask_app.get("/health")
def health():
    """Quick check that the proxy is alive."""
    return {"status": "ok", "service": "librarydesksense-proxy"}


def run_http():
    """Run the Flask server. Started on its own thread from main."""
    flask_app.run(host="0.0.0.0", port=HTTP_PORT, threaded=True)


def on_connect(client, userdata, flags, reason_code, properties=None):
    """MQTT: once connected, subscribe to this desk's events and config topics."""
    if reason_code != 0:
        print(f"[mqtt] connection refused: {reason_code}")
        return
    events_topic = topic("events")
    config_topic = topic("config")
    client.subscribe(events_topic, qos=1)
    client.subscribe(config_topic, qos=1)
    print(f"[mqtt] connected & subscribed: {events_topic}, {config_topic}")


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    """MQTT: paho reconnects by itself, we just log the drop."""
    print(f"[mqtt] disconnected ({reason_code}); retrying")


def on_message(client, userdata, message):
    """MQTT: route an incoming message to store_event or store_config_change by topic."""
    try:
        payload = json.loads(message.payload.decode())
    except ValueError:
        return
    if message.topic.endswith("/events"):
        store_event(payload)
    elif message.topic.endswith("/config"):
        store_config_change(payload)


def run_mqtt():
    """Run the MQTT client loop. Started on its own thread from main."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(MQTT_HOST, MQTT_PORT, 60)
    client.loop_forever(retry_first_connection=True)


class TelemetryResource(resource.Resource):
    async def render_post(self, request):
        """Read one CoAP telemetry payload and save it."""
        try:
            payload = json.loads(request.payload.decode())
            store_telemetry(payload, "coap")
            return aiocoap.Message(code=aiocoap.CHANGED)
        except (ValueError, TypeError) as error:
            print("[coap] bad payload", error)
            return aiocoap.Message(code=aiocoap.BAD_REQUEST)


async def run_coap():
    """Run the CoAP server on the main asyncio loop."""
    root = resource.Site()
    root.add_resource(["telemetry"], TelemetryResource())
    await aiocoap.Context.create_server_context(root, bind=(COAP_BIND_HOST, COAP_PORT))
    print(f"[coap] listening on udp/{COAP_PORT}")
    await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    threading.Thread(target=run_http, daemon=True).start()
    print(f"[http] listening on tcp/{HTTP_PORT}")
    threading.Thread(target=run_mqtt, daemon=True).start()

    asyncio.run(run_coap())
