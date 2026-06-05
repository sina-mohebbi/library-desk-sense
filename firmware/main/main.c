/*
================================================================
  main.c -- firmware entry point
================================================================
  This file starts Wi-Fi, MQTT, the sensor task, the telemetry
  task and the event publishing task.
*/
#include "shared.h"
#include "app_config.h"
#include "esp_log.h"
#include "nvs_flash.h"

static const char *TAG = "app";

app_cfg_t g_config;
SemaphoreHandle_t g_config_mutex;
QueueHandle_t g_telemetry_queue;
QueueHandle_t g_event_queue;
EventGroupHandle_t g_network_events;

/* Start the firmware and create the main tasks. */
void app_main(void) {
    ESP_ERROR_CHECK(nvs_flash_init());

    g_config = (app_cfg_t){
        .sampling_ms = DEFAULT_SAMPLING_MS,
        .noise_thr = DEFAULT_NOISE_THR,
        .light_thr = DEFAULT_LIGHT_THR,
        .occupancy_distance_cm = OCC_DISTANCE_CM,
        .occupancy_timeout_ms = DEFAULT_OCCUPANCY_TIMEOUT,
        .comm_mode = DEFAULT_COMM_MODE,
    };

    g_config_mutex = xSemaphoreCreateMutex();
    g_telemetry_queue = xQueueCreate(16, sizeof(telemetry_t));
    g_event_queue = xQueueCreate(16, sizeof(desk_event_t));
    g_network_events = xEventGroupCreate();

    wifi_init_sta();
    xEventGroupWaitBits(g_network_events, WIFI_CONNECTED_BIT,
                        pdFALSE, pdTRUE, portMAX_DELAY);
    mqtt_ctrl_start();

    sensors_init();
    xTaskCreate(sensing_task, "sensing", 4096, NULL, 5, NULL);
    xTaskCreate(telemetry_task, "telemetry", 6144, NULL, 5, NULL);
    xTaskCreate(event_pub_task, "event_pub", 4096, NULL, 5, NULL);

    ESP_LOGI(TAG, "LibraryDeskSense started (desk=%s)", DESK_ID);
}
