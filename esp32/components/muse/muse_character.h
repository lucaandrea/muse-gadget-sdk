#pragma once
#include "lvgl.h"
#include "muse_state.h"

lv_obj_t *muse_character_create(lv_obj_t *parent);
void muse_character_update(muse_mode_t mode, float now, float level, bool happy,
                           bool quiet, bool reduced, int size);
