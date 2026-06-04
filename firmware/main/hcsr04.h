/*
================================================================
  hcsr04.h -- distance sensor functions
================================================================
  This file declares the HC-SR04 helper functions.
*/
#pragma once

void hcsr04_init(void);
float hcsr04_read_cm(void);
float hcsr04_read_median_cm(void);
