/*
================================================================
  veml7700.c -- light sensor driver
================================================================
  This file talks to the VEML7700 light sensor over I2C.
*/
#include "veml7700.h"
#include "app_config.h"
#include "driver/i2c_master.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define VEML7700_REG_CONF 0x00
#define VEML7700_REG_ALS 0x04
#define VEML7700_RESOLUTION 0.0576f

static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_device;

/* Write one 16-bit register value to the sensor. */
static esp_err_t write_reg16(uint8_t register_addr, uint16_t value) {
    uint8_t bytes[3] = {register_addr, (uint8_t)(value & 0xFF), (uint8_t)(value >> 8)};
    return i2c_master_transmit(s_device, bytes, sizeof(bytes), 100);
}

/* Set up I2C and turn the light sensor on. */
esp_err_t veml7700_init(void) {
    i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_PORT,
        .sda_io_num = PIN_I2C_SDA,
        .scl_io_num = PIN_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t err = i2c_new_master_bus(&bus_config, &s_bus);
    if (err != ESP_OK) return err;

    i2c_device_config_t device_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = VEML7700_ADDR,
        .scl_speed_hz = 100000,
    };
    err = i2c_master_bus_add_device(s_bus, &device_config, &s_device);
    if (err != ESP_OK) return err;

    err = write_reg16(VEML7700_REG_CONF, 0x0000);
    vTaskDelay(pdMS_TO_TICKS(5));
    return err;
}

/* Read the light value in lux. */
esp_err_t veml7700_read_lux(float *lux_out) {
    uint8_t register_addr = VEML7700_REG_ALS, raw_bytes[2] = {0};
    esp_err_t err = i2c_master_transmit_receive(s_device, &register_addr, 1,
                                                raw_bytes, sizeof(raw_bytes), 100);
    if (err != ESP_OK) return err;
    uint16_t raw_counts = ((uint16_t)raw_bytes[1] << 8) | raw_bytes[0];
    *lux_out = raw_counts * VEML7700_RESOLUTION;
    return ESP_OK;
}
