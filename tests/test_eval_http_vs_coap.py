"""Tests for the latency/overhead summary in evaluation/eval_http_vs_coap.py."""
import eval_http_vs_coap as ev


def test_summarise_returns_none_when_no_requests_succeeded():
    assert ev.summarise("HTTP", [], ev.HTTP_HEADER_BYTES) is None


def test_summarise_computes_latency_stats():
    result = ev.summarise("HTTP", [10.0, 20.0, 30.0], ev.HTTP_HEADER_BYTES)
    assert result["successful_requests"] == 3
    assert result["mean_ms"] == 20.0
    assert result["median_ms"] == 20.0
    assert result["p95_ms"] >= result["median_ms"]


def test_summarise_bytes_per_message_is_header_plus_payload():
    result = ev.summarise("CoAP", [5.0], ev.COAP_HEADER_BYTES)
    assert result["payload_bytes"] == len(ev.BODY)
    assert result["bytes_per_message"] == ev.COAP_HEADER_BYTES + len(ev.BODY)


def test_coap_has_lower_header_overhead_than_http():
    # the whole point of the benchmark: CoAP's header is much smaller than HTTP's
    assert ev.COAP_HEADER_BYTES < ev.HTTP_HEADER_BYTES
