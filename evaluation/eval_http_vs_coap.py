"""
================================================================
  eval_http_vs_coap.py -- protocol comparison
================================================================
  This file sends the same sample by HTTP and CoAP, then compares
  latency and simple application-layer byte overhead.
"""
import argparse
import asyncio
import json
import statistics as stats
import time
from pathlib import Path

import numpy as np
import requests
import aiocoap

PAYLOAD = {"desk": "benchmark-01", "ts": 0, "occupied": True,
           "session_s": 42, "noise": 1234, "lux": 321.0}
BODY = json.dumps(PAYLOAD).encode()

HTTP_HEADER_BYTES = 180
COAP_HEADER_BYTES = 16
COAP_JSON_FORMAT = 50


def bench_http(url, message_count):
    """Time HTTP POSTs and return the successful latencies in ms."""
    latencies = []
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(message_count):
            start_time = time.perf_counter()
            try:
                response = session.post(url, data=BODY,
                                        headers={"Content-Type": "application/json"}, timeout=5)
                if response.status_code < 400:
                    latencies.append((time.perf_counter() - start_time) * 1000)
            except requests.RequestException:
                pass
    return latencies


async def bench_coap(uri, message_count):
    """Time `message_count` CoAP POSTs and return the successful latencies (ms)."""
    latencies = []
    context = await aiocoap.Context.create_client_context()
    for _ in range(message_count):
        request_message = aiocoap.Message(code=aiocoap.POST, uri=uri, payload=BODY)
        request_message.opt.content_format = COAP_JSON_FORMAT
        start_time = time.perf_counter()
        try:
            response = await context.request(request_message).response
            if response.code.is_successful():
                latencies.append((time.perf_counter() - start_time) * 1000)
        except Exception:
            pass
    await context.shutdown()
    return latencies


def summarise(name, latencies, header_bytes):
    """Print and return latency stats + the estimated bytes per message."""
    if not latencies:
        print(f"{name:5s}: no successful requests (is the proxy running?)")
        return None
    result = {
        "successful_requests": len(latencies),
        "mean_ms": float(stats.mean(latencies)),
        "median_ms": float(stats.median(latencies)),
        "p95_ms": float(np.percentile(latencies, 95)),
        "header_bytes": header_bytes,
        "payload_bytes": len(BODY),
        "bytes_per_message": header_bytes + len(BODY),
    }
    print(f"{name:5s}: mean={result['mean_ms']:6.1f} ms  median={result['median_ms']:6.1f} ms  "
          f"p95={result['p95_ms']:6.1f} ms  ~{result['bytes_per_message']} B/msg "
          f"(header {header_bytes} + body {len(BODY)})")
    return result


def run_benchmark(host, message_count, output):
    """Run both protocols against the proxy and save the results as JSON."""
    http_url = f"http://{host}:8080/telemetry"
    coap_uri = f"coap://{host}/telemetry"

    print(f"Benchmarking {message_count} messages per protocol against {host}...\n")
    http_latencies = bench_http(http_url, message_count)
    coap_latencies = asyncio.run(bench_coap(coap_uri, message_count))

    print("\n=== Latency & overhead ===")
    http_result = summarise("HTTP", http_latencies, HTTP_HEADER_BYTES)
    coap_result = summarise("CoAP", coap_latencies, COAP_HEADER_BYTES)

    results = {
        "messages_per_protocol": message_count,
        "payload": PAYLOAD,
        "overhead_note": "Application-layer byte estimates; IP/TCP/UDP framing excluded.",
        "http": http_result,
        "coap": coap_result,
    }
    Path(output).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"saved {output}")
    return results


def main():
    """Run the protocol benchmark from the command line."""
    parser = argparse.ArgumentParser(description="HTTP vs CoAP latency/overhead benchmark.")
    parser.add_argument("--host", default="127.0.0.1", help="Proxy IP address.")
    parser.add_argument("--n", dest="message_count", type=int, default=100,
                        help="Messages per protocol.")
    parser.add_argument("--output", default="evaluation_results.json")
    args = parser.parse_args()
    run_benchmark(args.host, args.message_count, args.output)


if __name__ == "__main__":
    main()
