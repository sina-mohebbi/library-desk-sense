"""
================================================================
  config_publisher.py  --  runtime configuration CLI
================================================================
  Small command-line tool. Sends a config change to the proxy's
  /config endpoint; the proxy then relays it to the ESP32 over
  MQTT. Only the flags you pass are sent, so you can change one
  setting at a time without a reflash.
"""
import argparse

import requests
from common import DESK_ID, HTTP_PORT


def parse_args():
    """Read the command-line flags for one config change."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--desk", default=DESK_ID)
    parser.add_argument("--sampling-ms", type=int)
    parser.add_argument("--noise-thr", type=int)
    parser.add_argument("--light-thr", type=int)
    parser.add_argument("--occupancy-distance-cm", type=int)
    parser.add_argument("--occupancy-timeout-ms", type=int)
    parser.add_argument("--comm-mode", choices=["http", "coap", "auto"])
    parser.add_argument("--proxy-url", default=f"http://127.0.0.1:{HTTP_PORT}/config")
    return parser.parse_args()


def build_config(args):
    """Build a config dict from only the flags the user passed."""
    config = {"desk": args.desk}
    config_field_names = ["sampling_ms", "noise_thr", "light_thr", "occupancy_distance_cm", "occupancy_timeout_ms", "comm_mode"]
    for field_name in config_field_names:
        value = getattr(args, field_name)
        if value is not None:
            config[field_name] = value
    return config


def main():
    """Send the config change to the proxy."""
    args = parse_args()
    config = build_config(args)
    response = requests.post(args.proxy_url, json=config, timeout=10)
    response.raise_for_status()
    result = response.json()
    print("published", result["config"], "->", result["topic"])


if __name__ == "__main__":
    main()
