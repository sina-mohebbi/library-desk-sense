/*
================================================================
  veml7700.h -- light sensor functions
================================================================
  This file declares the VEML7700 helper functions.
*/
#pragma once
#include "esp_err.h"
esp_err_t veml7700_init(void);
esp_err_t veml7700_read_lux(float *lux_out);
