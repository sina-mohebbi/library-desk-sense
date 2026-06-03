/*
================================================================
  shared.h -- shared firmware types
================================================================
  This file keeps the common structs, queues, event bits and task
  declarations used by the firmware modules.
*/
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"

typedef enum {COMM_HTTP = 0, COMM_COAP = 1, COMM_AUTO = 2} comm_mode_t;

typedef struct {
    uint32_t sampling_ms;
    int noise_thr;
    int light_thr;
    int occupancy_distance_cm;
    uint32_t occupancy_timeout_ms;
    comm_mode_t comm_mode;
} app_cfg_t;

typedef struct {
    int64_t ts_ms;
    bool occupied;
    uint32_t session_s;
    int noise_level;
    float light_lux;
} telemetry_t;

typedef enum {EV_DESK_OCCUPIED = 0, EV_DESK_RELEASED, EV_HIGH_NOISE, EV_POOR_LIGHTING} event_type_t;

typedef struct {
    event_type_t type;
    int64_t ts_ms;
    float value;
} desk_event_t;

extern app_cfg_t g_config;
extern SemaphoreHandle_t g_config_mutex;
extern QueueHandle_t g_telemetry_queue;
extern QueueHandle_t g_event_queue;
extern EventGroupHandle_t g_network_events;
#define WIFI_CONNECTED_BIT BIT0
#define MQTT_CONNECTED_BIT BIT1

/* Take a safe copy of the current config. */
static inline app_cfg_t config_snapshot(void) {
    app_cfg_t snapshot;
    xSemaphoreTake(g_config_mutex, portMAX_DELAY);
    snapshot = g_config;
    xSemaphoreGive(g_config_mutex);
    return snapshot;
}

void wifi_init_sta(void);
void sensors_init(void);
void sensing_task(void *arg);
void telemetry_task(void *arg);
void mqtt_ctrl_start(void);
void event_pub_task(void *arg);
