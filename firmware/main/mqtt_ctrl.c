/*
================================================================
  mqtt_ctrl.c -- MQTT events and config
================================================================
  This file publishes desk events and listens for runtime config
  changes from the proxy.
*/
#include "shared.h"
#include "app_config.h"
#include "esp_log.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include <string.h>
#include <stdio.h>

static const char *TAG = "mqtt";
static esp_mqtt_client_handle_t s_mqtt_client;
#define TOPIC_EVENTS "librarydesksense/" DESK_ID "/events"
#define TOPIC_CONFIG "librarydesksense/" DESK_ID "/config"

/* Turn an event type into the name we put in the MQTT message. */
static const char *event_name(event_type_t type) {
    switch (type) {
        case EV_DESK_OCCUPIED: return "desk_occupied";
        case EV_DESK_RELEASED: return "desk_released";
        case EV_HIGH_NOISE: return "high_noise_event";
        case EV_POOR_LIGHTING: return "poor_lighting_event";
        default: return "unknown";
    }
}

/* Read a config message and apply the values that are in range, under the mutex. */
static void apply_config(const char *json_data, int json_len) {
    cJSON *root = cJSON_ParseWithLength(json_data, json_len);
    if (!root) {
        ESP_LOGW(TAG, "bad config json");
        return;
    }
    xSemaphoreTake(g_config_mutex, portMAX_DELAY);
    cJSON *item;
    if ((item = cJSON_GetObjectItem(root, "sampling_ms")) && cJSON_IsNumber(item) &&
        item->valuedouble >= 100 && item->valuedouble <= 3600000)
        g_config.sampling_ms = (uint32_t)item->valuedouble;
    if ((item = cJSON_GetObjectItem(root, "noise_thr")) && cJSON_IsNumber(item) &&
        item->valueint >= 0 && item->valueint <= 4095)
        g_config.noise_thr = item->valueint;
    if ((item = cJSON_GetObjectItem(root, "light_thr")) && cJSON_IsNumber(item) &&
        item->valueint >= 0 && item->valueint <= 200000)
        g_config.light_thr = item->valueint;
    if ((item = cJSON_GetObjectItem(root, "occupancy_distance_cm")) && cJSON_IsNumber(item) &&
        item->valueint >= 5 && item->valueint <= 400)
        g_config.occupancy_distance_cm = item->valueint;
    if ((item = cJSON_GetObjectItem(root, "occupancy_timeout_ms")) && cJSON_IsNumber(item) &&
        item->valuedouble >= 1000 && item->valuedouble <= 86400000)
        g_config.occupancy_timeout_ms = (uint32_t)item->valuedouble;
    if ((item = cJSON_GetObjectItem(root, "comm_mode")) && cJSON_IsString(item)) {
        if (!strcmp(item->valuestring, "http")) g_config.comm_mode = COMM_HTTP;
        else if (!strcmp(item->valuestring, "coap")) g_config.comm_mode = COMM_COAP;
        else if (!strcmp(item->valuestring, "auto")) g_config.comm_mode = COMM_AUTO;
    }
    app_cfg_t applied = g_config;
    xSemaphoreGive(g_config_mutex);
    cJSON_Delete(root);
    const char *mode_name = applied.comm_mode == COMM_HTTP ? "http" :
                            applied.comm_mode == COMM_COAP ? "coap" : "auto";
    ESP_LOGI(TAG, "config applied: sampling=%lums noise_thr=%d light_thr=%d "
             "occupancy_distance=%dcm occupancy_timeout=%lums comm=%s",
             (unsigned long)applied.sampling_ms, applied.noise_thr, applied.light_thr,
             applied.occupancy_distance_cm,
             (unsigned long)applied.occupancy_timeout_ms, mode_name);
}

/* Handle MQTT connection, disconnection, errors and config messages. */
static void mqtt_event_handler(void *handler_arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    esp_mqtt_event_handle_t event = event_data;
    switch ((esp_mqtt_event_id_t)event_id) {
        case MQTT_EVENT_CONNECTED:
            esp_mqtt_client_subscribe(s_mqtt_client, TOPIC_CONFIG, 1);
            xEventGroupSetBits(g_network_events, MQTT_CONNECTED_BIT);
            break;
        case MQTT_EVENT_DISCONNECTED:
            xEventGroupClearBits(g_network_events, MQTT_CONNECTED_BIT);
            break;
        case MQTT_EVENT_ERROR:
            ESP_LOGE(TAG, "connection error (type=%d, tls=0x%x, socket_errno=%d)",
                     event->error_handle ? event->error_handle->error_type : -1,
                     event->error_handle ? event->error_handle->esp_tls_last_esp_err : 0,
                     event->error_handle ? event->error_handle->esp_transport_sock_errno : 0);
            break;
        case MQTT_EVENT_DATA:
            if (event->topic_len == strlen(TOPIC_CONFIG) &&
                !memcmp(event->topic, TOPIC_CONFIG, event->topic_len))
                apply_config(event->data, event->data_len);
            break;
        default: break;
    }
}

/* Create and start the MQTT client. */
void mqtt_ctrl_start(void) {
    esp_mqtt_client_config_t client_config = {
        .broker.address.uri = MQTT_BROKER_URI,
        .credentials.client_id = DESK_ID,
    };
    s_mqtt_client = esp_mqtt_client_init(&client_config);
    esp_mqtt_client_register_event(s_mqtt_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(s_mqtt_client);
}

/* Wait for events on the queue and publish each one to the MQTT events topic. */
void event_pub_task(void *arg) {
    desk_event_t event;
    char buffer[160];

    for (;;) {
        if (xQueueReceive(g_event_queue, &event, portMAX_DELAY) != pdTRUE) continue;
        xEventGroupWaitBits(g_network_events, MQTT_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);

        snprintf(buffer, sizeof(buffer),
            "{\"desk\":\"%s\",\"event\":\"%s\",\"value\":%.1f,\"ts\":%lld}",
            DESK_ID, event_name(event.type), event.value, (long long)event.ts_ms);

        if (esp_mqtt_client_publish(s_mqtt_client, TOPIC_EVENTS, buffer, 0, 1, 0) < 0)
            ESP_LOGE(TAG, "event publish failed -> %s", buffer);
        else
            ESP_LOGI(TAG, "event -> %s", buffer);
    }
}
