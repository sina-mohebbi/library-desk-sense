/*
================================================================
  telemetry.c -- send telemetry to the proxy
================================================================
  This file sends each sample by HTTP, CoAP or AUTO mode and
  remembers which protocol is faster.
*/
#include "shared.h"
#include "app_config.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_http_client.h"
#include <string.h>
#include <stdio.h>
#include <inttypes.h>
#include "coap3/coap.h"

static const char *TAG = "telemetry";
static float s_http_latency_ms = 1000.0f, s_coap_latency_ms = 1000.0f;
static const float LATENCY_SMOOTHING = 0.3f;
static bool s_http_tried = false, s_coap_tried = false;
static uint32_t s_auto_sample_count = 0;

/* Short protocol name for the logs. */
static const char *protocol_name(comm_mode_t mode) {
    if (mode == COMM_COAP) return "CoAP";
    if (mode == COMM_HTTP) return "HTTP";
    return "AUTO";
}

/* Build the JSON payload that the proxy expects. */
static int build_json(char *buffer, size_t buffer_size, const telemetry_t *sample) {
    return snprintf(buffer, buffer_size,
        "{\"desk\":\"%s\",\"ts\":%" PRId64 ",\"occupied\":%s,\"session_s\":%" PRIu32 ","
        "\"noise\":%d,\"lux\":%.1f}",
        DESK_ID,
        sample->ts_ms,
        sample->occupied ? "true" : "false",
        sample->session_s,
        sample->noise_level,
        sample->light_lux);
}

/* POST the json over HTTP and time it. Return the round-trip in ms, or -1 if it failed. */
static float send_http(const char *json_payload) {
    int64_t start_time = esp_timer_get_time();

    esp_http_client_config_t config;
    memset(&config, 0, sizeof(config));
    config.url = HTTP_TELEMETRY_URL;
    config.method = HTTP_METHOD_POST;
    config.timeout_ms = 4000;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, json_payload, strlen(json_payload));

    esp_err_t perform_result = esp_http_client_perform(client);
    int status_code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (perform_result != ESP_OK || status_code >= 400) return -1.0f;
    return (float)(esp_timer_get_time() - start_time) / 1000.0f;
}

static volatile bool s_coap_response_received, s_coap_response_ok;

/* libcoap calls this when the reply arrives. Just note whether it was a 2.xx (ok). */
static coap_response_t coap_response_handler(coap_session_t *session, const coap_pdu_t *sent,
    const coap_pdu_t *received, const coap_mid_t message_id) {
    (void)session; (void)sent; (void)message_id;
    s_coap_response_ok = (coap_pdu_get_code(received) >> 5) == 2;
    s_coap_response_received = true;
    return COAP_RESPONSE_OK;
}

/* Send the json over CoAP and wait for the reply. Return the round-trip in ms, or -1 if it failed. */
static float send_coap(const char *json_payload) {
    int64_t start_time = esp_timer_get_time();
    float round_trip_ms = -1.0f;

    coap_uri_t uri;
    if (coap_split_uri((const uint8_t *)COAP_TELEMETRY_URI, strlen(COAP_TELEMETRY_URI), &uri) < 0)
        return -1.0f;

    coap_addr_info_t *address_info = coap_resolve_address_info(
        &uri.host, uri.port, uri.port, uri.port, uri.port,
        0, 1 << uri.scheme, COAP_RESOLVE_TYPE_REMOTE);
    if (address_info == NULL) return -1.0f;

    coap_address_t destination = address_info->addr;
    coap_free_address_info(address_info);

    coap_context_t *context = coap_new_context(NULL);
    if (context == NULL) return -1.0f;

    coap_session_t *session = coap_new_client_session(context, NULL, &destination, COAP_PROTO_UDP);
    if (session == NULL) {
        coap_free_context(context);
        return -1.0f;
    }

    coap_register_response_handler(context, coap_response_handler);

    coap_pdu_t *request_pdu = coap_new_pdu(COAP_MESSAGE_CON, COAP_REQUEST_CODE_POST, session);
    if (request_pdu == NULL) {
        coap_session_release(session);
        coap_free_context(context);
        return -1.0f;
    }

    coap_add_option(request_pdu, COAP_OPTION_URI_PATH, uri.path.length, uri.path.s);
    uint8_t content_format = COAP_MEDIATYPE_APPLICATION_JSON;
    coap_add_option(request_pdu, COAP_OPTION_CONTENT_FORMAT, 1, &content_format);
    coap_add_data(request_pdu, strlen(json_payload), (const uint8_t *)json_payload);

    s_coap_response_received = false;
    s_coap_response_ok = false;

    if (coap_send(session, request_pdu) != COAP_INVALID_MID) {
        for (int poll_index = 0; poll_index < 40 && !s_coap_response_received; poll_index++)
            coap_io_process(context, 100);
        if (s_coap_response_received && s_coap_response_ok)
            round_trip_ms = (float)(esp_timer_get_time() - start_time) / 1000.0f;
    }

    coap_session_release(session);
    coap_free_context(context);
    return round_trip_ms;
}

/* Pick a protocol for AUTO mode and sometimes test the other one. */
static comm_mode_t choose_auto_protocol(void) {
    if (!s_http_tried) return COMM_HTTP;
    if (!s_coap_tried) return COMM_COAP;

    s_auto_sample_count++;
    bool probe_other_protocol = (s_auto_sample_count % 10) == 0;
    bool coap_is_faster = s_coap_latency_ms <= s_http_latency_ms;

    if (probe_other_protocol) return coap_is_faster ? COMM_HTTP : COMM_COAP;
    return coap_is_faster ? COMM_COAP : COMM_HTTP;
}

/* Pick the protocol for this sample: fixed HTTP/CoAP, or let AUTO decide. */
static comm_mode_t choose_protocol(const app_cfg_t *config) {
    if (config->comm_mode == COMM_HTTP) return COMM_HTTP;
    if (config->comm_mode == COMM_COAP) return COMM_COAP;
    return choose_auto_protocol();
}

/* It worked, so blend the new latency into that protocol's average (30% new, 70% old). */
static void remember_success(comm_mode_t protocol, float latency_ms) {
    if (protocol == COMM_COAP) {
        s_coap_latency_ms = LATENCY_SMOOTHING * latency_ms + (1.0f - LATENCY_SMOOTHING) * s_coap_latency_ms;
        s_coap_tried = true;
    } else {
        s_http_latency_ms = LATENCY_SMOOTHING * latency_ms + (1.0f - LATENCY_SMOOTHING) * s_http_latency_ms;
        s_http_tried = true;
    }
}

/* It failed, so give that protocol a big latency so AUTO avoids it for a while. */
static void remember_failure(comm_mode_t protocol) {
    if (protocol == COMM_COAP) {
        s_coap_latency_ms = 4000.0f;
        s_coap_tried = true;
    } else {
        s_http_latency_ms = 4000.0f;
        s_http_tried = true;
    }
}

/* Wait for a sample, send it with the chosen protocol (retry on the other one in AUTO), then log. */
void telemetry_task(void *arg) {
    char json_buffer[256];
    telemetry_t sample;

    for (;;) {
        if (xQueueReceive(g_telemetry_queue, &sample, portMAX_DELAY) != pdTRUE) continue;

        xEventGroupWaitBits(g_network_events, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
        build_json(json_buffer, sizeof(json_buffer), &sample);

        app_cfg_t config = config_snapshot();
        bool auto_mode = config.comm_mode == COMM_AUTO;

        comm_mode_t protocol = choose_protocol(&config);
        float latency_ms = protocol == COMM_COAP ? send_coap(json_buffer) : send_http(json_buffer);

        if (latency_ms >= 0.0f) {
            remember_success(protocol, latency_ms);
        } else if (auto_mode) {
            remember_failure(protocol);
            protocol = protocol == COMM_COAP ? COMM_HTTP : COMM_COAP;
            latency_ms = protocol == COMM_COAP ? send_coap(json_buffer) : send_http(json_buffer);
            if (latency_ms >= 0.0f) remember_success(protocol, latency_ms);
        }

        ESP_LOGI(TAG, "sent via %s lat=%.1fms (ewma http=%.0f coap=%.0f)",
                 protocol_name(protocol), latency_ms, s_http_latency_ms, s_coap_latency_ms);
    }
}
