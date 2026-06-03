/*
================================================================
  app_config.h -- fixed project settings
================================================================
  This file keeps the Wi-Fi, proxy, sensor pin and threshold
  values used by the firmware.
*/
#pragma once

#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASS "YOUR_WIFI_PASSWORD"

#define PROXY_HOST "YOUR_PROXY_IP"
#define HTTP_TELEMETRY_URL "http://" PROXY_HOST ":8080/telemetry"
#define COAP_TELEMETRY_URI "coap://" PROXY_HOST "/telemetry"
#define MQTT_BROKER_URI "mqtt://" PROXY_HOST ":1883"

#define DESK_ID "desk-01"

#define PIN_TRIG 5
#define PIN_ECHO 18

#define I2C_PORT 0
#define PIN_I2C_SDA 21
#define PIN_I2C_SCL 22
#define VEML7700_ADDR 0x10

#define SOUND_ADC_UNIT   ADC_UNIT_1
#define SOUND_ADC_CHANNEL  ADC_CHANNEL_7

#define OCC_DISTANCE_CM  50
#define OCC_CONFIRM_SAMPLES  3

#define DEFAULT_SAMPLING_MS 2000
#define DEFAULT_NOISE_THR 100
#define DEFAULT_LIGHT_THR 150
#define DEFAULT_OCCUPANCY_TIMEOUT 15000
#define DEFAULT_COMM_MODE COMM_AUTO

#define SOUND_SAMPLES 256
#define SOUND_SAMPLE_INTERVAL_US 400

#define ENV_CONFIRM_SAMPLES 2
#define NOISE_CLEAR_PERCENT 80
#define LIGHT_CLEAR_MARGIN_LUX 20
