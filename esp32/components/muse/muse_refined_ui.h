#pragma once
#include "lvgl.h"
#include "muse_state.h"
bool muse_refined_build(lv_obj_t *parent, int width, int height);
void muse_refined_tick(muse_mode_t mode, float now, float level);
bool muse_refined_preview(const char *name);
#if LV_USE_SNAPSHOT
bool muse_refined_check(const char *property, int expected);
#endif
