/*
================================================================
  sensors.c -- desk sensing task
================================================================
  This file reads distance, light and sound, then decides if the
  desk is occupied and if an event should be sent.
*/
#include "shared.h"
#include "app_config.h"
#include "veml7700.h"
#include "hcsr04.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include <stdlib.h>
#include <string.h>
#include "esp_adc/adc_oneshot.h"

static adc_oneshot_unit_handle_t s_adc_handle;
static const char *TAG = "sensors";
static bool s_occupied = false;
static int64_t s_session_start_ms = 0;
static int64_t s_last_present_ms = 0;
static int64_t s_first_confirmed_present_ms = 0;
static uint8_t s_present_count = 0;
static bool s_high_noise_active = false;
static bool s_poor_light_active = false;
static uint8_t s_light_low_count = 0;
static uint8_t s_light_clear_count = 0;

/* Set up the distance, light and sound sensors. */
void sensors_init(void) {
    hcsr04_init();

    if (veml7700_init() != ESP_OK)
        ESP_LOGE(TAG, "VEML7700 init failed");

    adc_oneshot_unit_init_cfg_t unit_config;
    memset(&unit_config, 0, sizeof(unit_config));
    unit_config.unit_id = SOUND_ADC_UNIT;
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&unit_config, &s_adc_handle));

    adc_oneshot_chan_cfg_t channel_config;
    memset(&channel_config, 0, sizeof(channel_config));
    channel_config.atten = ADC_ATTEN_DB_12;
    channel_config.bitwidth = ADC_BITWIDTH_12;
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc_handle, SOUND_ADC_CHANNEL, &channel_config));
}

/* Read the light in lux, or return -1 if the I2C read fails. */
static float read_light(void) {
    float lux = 0.0f;
    return veml7700_read_lux(&lux) == ESP_OK ? lux : -1.0f;
}

/* Integer square root for the RMS noise calculation. */
static uint32_t isqrt_u32(uint32_t value) {
    uint32_t root = 0;
    uint32_t bit = 1UL << 30;
    while (bit > value) bit >>= 2;
    while (bit != 0) {
        if (value >= root + bit) {
            value -= root + bit;
            root = (root >> 1) + bit;
        } else {
            root >>= 1;
        }
        bit >>= 2;
    }
    return root;
}

/* Read the mic many times and return the RMS sound intensity in this interval. */
static int read_noise(void) {
    int adc_samples[SOUND_SAMPLES];
    int sum = 0;
    for (int sample_index = 0; sample_index < SOUND_SAMPLES; sample_index++) {
        int adc_value = 0;
        adc_oneshot_read(s_adc_handle, SOUND_ADC_CHANNEL, &adc_value);
        adc_samples[sample_index] = adc_value;
        sum += adc_value;
        esp_rom_delay_us(SOUND_SAMPLE_INTERVAL_US);
    }
    int mean_level = sum / SOUND_SAMPLES;
    uint64_t mean_square = 0;
    for (int sample_index = 0; sample_index < SOUND_SAMPLES; sample_index++) {
        int deviation = abs(adc_samples[sample_index] - mean_level);
        mean_square += (uint64_t)(deviation * deviation);
    }
    return (int)isqrt_u32((uint32_t)(mean_square / SOUND_SAMPLES));
}

/* Put one desk event on the queue. */
static void push_event(event_type_t type, float value, int64_t timestamp_ms) {
    desk_event_t event = {.type = type, .ts_ms = timestamp_ms, .value = value};
    if (xQueueSend(g_event_queue, &event, 0) != pdTRUE)
        ESP_LOGW(TAG, "event queue full; dropped one event");
}

/* Decide if the desk is occupied after steady readings. */
static void update_occupancy_state(bool present, float distance,
                                   const app_cfg_t *config, int64_t now_ms) {
    if (present) {
        s_last_present_ms = now_ms;
        if (!s_occupied) {
            if (s_present_count == 0) s_first_confirmed_present_ms = now_ms;
            if (s_present_count < OCC_CONFIRM_SAMPLES) s_present_count++;
        }
    } else if (!s_occupied) {
        s_present_count = 0;
        s_first_confirmed_present_ms = 0;
    }

    if (!s_occupied) {
        if (s_present_count >= OCC_CONFIRM_SAMPLES) {
            s_occupied = true;
            s_session_start_ms = s_first_confirmed_present_ms;
            s_present_count = 0;
            push_event(EV_DESK_OCCUPIED, distance, now_ms);
        }
    } else if (now_ms - s_last_present_ms > config->occupancy_timeout_ms) {
        int64_t duration_ms = s_last_present_ms - s_session_start_ms;
        uint32_t duration_seconds = duration_ms > 0 ? (uint32_t)(duration_ms / 1000) : 0;
        s_occupied = false;
        s_present_count = 0;
        s_first_confirmed_present_ms = 0;
        push_event(EV_DESK_RELEASED, duration_seconds, now_ms);
    }
}

/* How long the desk has been occupied, in seconds, or 0 if it's free. */
static uint32_t current_session_seconds(int64_t now_ms) {
    if (!s_occupied || now_ms <= s_session_start_ms) return 0;
    return (uint32_t)((now_ms - s_session_start_ms) / 1000);
}

/* Send one high-noise event when noise crosses the threshold. */
static void update_noise_event(int noise, const app_cfg_t *config, int64_t now_ms) {
    int clear_threshold = (config->noise_thr * NOISE_CLEAR_PERCENT) / 100;
    if (!s_high_noise_active && noise > config->noise_thr) {
        push_event(EV_HIGH_NOISE, noise, now_ms);
        s_high_noise_active = true;
    } else if (s_high_noise_active && noise <= clear_threshold) {
        s_high_noise_active = false;
    }
}

/* Send one poor-light event after the light stays too low. */
static void update_light_event(float lux, const app_cfg_t *config, int64_t now_ms) {
    if (lux < 0.0f) return;

    if (!s_poor_light_active) {
        if (lux >= config->light_thr) {
            s_light_low_count = 0;
        } else if (++s_light_low_count >= ENV_CONFIRM_SAMPLES) {
            push_event(EV_POOR_LIGHTING, lux, now_ms);
            s_poor_light_active = true;
            s_light_low_count = 0;
            s_light_clear_count = 0;
        }
    } else {
        if (lux < config->light_thr + LIGHT_CLEAR_MARGIN_LUX)
            s_light_clear_count = 0;
        else if (++s_light_clear_count >= ENV_CONFIRM_SAMPLES) {
            s_poor_light_active = false;
            s_light_clear_count = 0;
        }
    }
}

/* Pack one reading and send it to the telemetry task. */
static void queue_telemetry(int64_t now_ms, int noise, float lux, uint32_t session_seconds) {
    telemetry_t sample = {
        .ts_ms = now_ms,
        .occupied = s_occupied,
        .session_s = session_seconds,
        .noise_level = noise,
        .light_lux = lux,
    };
    if (xQueueSend(g_telemetry_queue, &sample, 0) != pdTRUE)
        ESP_LOGW(TAG, "telemetry queue full; dropped one sample");
}

/* Main loop: read every sensor, update the occupancy/noise/light state, and queue a sample. */
void sensing_task(void *arg) {
    for (;;) {
        app_cfg_t config = config_snapshot();
        int64_t now_ms = esp_timer_get_time() / 1000;
        float distance = hcsr04_read_median_cm();
        bool distance_valid = distance > 0.0f;
        bool present = distance_valid && distance < config.occupancy_distance_cm;
        float lux = read_light();
        int noise = read_noise();

        if (distance_valid)
            update_occupancy_state(present, distance, &config, now_ms);
        uint32_t session_seconds = current_session_seconds(now_ms);
        update_noise_event(noise, &config, now_ms);
        update_light_event(lux, &config, now_ms);
        queue_telemetry(now_ms, noise, lux, session_seconds);

        if (distance_valid)
            ESP_LOGI(TAG, "distance=%.1fcm present=%d occ=%d sess=%us noise=%d lux=%.1f",
                     distance, present, s_occupied, session_seconds, noise, lux);
        else
            ESP_LOGW(TAG, "HC-SR04 no valid echo; occ=%d sess=%us noise=%d lux=%.1f",
                     s_occupied, session_seconds, noise, lux);
        vTaskDelay(pdMS_TO_TICKS(config.sampling_ms));
    }
}
