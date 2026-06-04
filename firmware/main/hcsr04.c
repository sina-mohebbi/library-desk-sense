/*
================================================================
  hcsr04.c -- distance sensor driver
================================================================
  This file measures distance by timing the HC-SR04 ECHO pulse.
*/
#include "hcsr04.h"
#include "app_config.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "rom/ets_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* Set up the TRIG and ECHO pins. */
void hcsr04_init(void) {
    gpio_set_direction(PIN_TRIG, GPIO_MODE_OUTPUT);
    gpio_set_direction(PIN_ECHO, GPIO_MODE_INPUT);
    gpio_set_pull_mode(PIN_ECHO, GPIO_PULLDOWN_ONLY);
    gpio_set_level(PIN_TRIG, 0);
}

/* Read one distance value in centimeters. */
float hcsr04_read_cm(void) {
    gpio_set_level(PIN_TRIG, 0); ets_delay_us(3);
    gpio_set_level(PIN_TRIG, 1); ets_delay_us(10);
    gpio_set_level(PIN_TRIG, 0);

    int64_t deadline = esp_timer_get_time() + 30000;
    while (gpio_get_level(PIN_ECHO) == 0)
        if (esp_timer_get_time() > deadline) return -1.0f;

    int64_t echo_start = esp_timer_get_time();
    deadline = echo_start + 30000;
    while (gpio_get_level(PIN_ECHO) == 1)
        if (esp_timer_get_time() > deadline) return -1.0f;

    float distance_cm = (float)(esp_timer_get_time() - echo_start) * 0.0343f / 2.0f;
    if (distance_cm < 2.0f || distance_cm > 400.0f) return -1.0f;
    return distance_cm;
}

/* Read three values and return the middle one. */
float hcsr04_read_median_cm(void) {
    float readings[3];
    int valid_count = 0;

    for (int reading_index = 0; reading_index < 3; reading_index++) {
        float distance = hcsr04_read_cm();
        if (distance > 0.0f) readings[valid_count++] = distance;
        if (reading_index < 2) vTaskDelay(pdMS_TO_TICKS(60));
    }

    if (valid_count == 0) return -1.0f;

    for (int left_index = 0; left_index < valid_count; left_index++)
        for (int right_index = left_index + 1; right_index < valid_count; right_index++)
            if (readings[right_index] < readings[left_index]) {
                float swap = readings[left_index];
                readings[left_index] = readings[right_index];
                readings[right_index] = swap;
            }

    return readings[valid_count / 2];
}
