/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "sim_board.h"
#include "sim_platform.h"
#include <string.h>

#include "src/drivers/sdl/lv_sdl_mouse.h"
#include "src/drivers/sdl/lv_sdl_window.h"

#define WATCHER_RESOLUTION 412

static lv_display_t *s_display;
static int s_resolution = WATCHER_RESOLUTION;

/* muse_ui.c reads the selected board through this production global. */
const muse_board_t *muse_board;

static esp_err_t sim_init(void)
{
    return ESP_OK;
}

static lv_display_t *sim_display_start(lv_indev_t **touch)
{
    s_display = lv_sdl_window_create(s_resolution, s_resolution);
    if (!s_display) {
        return NULL;
    }
    /* The SDL driver installs SDL_GetTicks. Use the simulator clock instead so
     * scripted runs can advance time without sleeping and render repeatably. */
    lv_tick_set_cb(sim_time_tick_ms);
    lv_sdl_window_set_title(s_display, "Muse Gadget Simulator");
    lv_sdl_window_set_resizeable(s_display, false);
    if (touch) {
        *touch = lv_sdl_mouse_create();
    }
    return s_display;
}

static bool sim_display_lock(int timeout_ms)
{
    (void)timeout_ms;
    return true;
}

static void sim_display_unlock(void)
{
}

static void sim_set_brightness(int pct)
{
    (void)pct;
}

static void sim_panel_sleep(bool sleep)
{
    (void)sleep;
}

static esp_err_t sim_power_off(void)
{
    return ESP_FAIL;
}

/* Advertise the real board's audio controls without opening host audio. */
static esp_err_t sim_audio_init(esp_codec_dev_handle_t *spk, esp_codec_dev_handle_t *mic)
{
    *spk = NULL;
    *mic = NULL;
    return ESP_FAIL;
}

static const muse_board_t s_default_board = {
    .name = "SenseCAP Watcher Simulator",
    .width = WATCHER_RESOLUTION,
    .height = WATCHER_RESOLUTION,
    .round = true,
    .touch = true,
    .diagonal_in = 1.45f,
    .talk_button = "wheel",
    .aux_button = "scroll",
    .talk_hint = { LV_ALIGN_CENTER, 100, -143 },
    .frame_ms = 40,
    .init = sim_init,
    .display_start = sim_display_start,
    .display_lock = sim_display_lock,
    .display_unlock = sim_display_unlock,
    .set_brightness = sim_set_brightness,
    .panel_sleep = sim_panel_sleep,
    .power_off = sim_power_off,
};
static muse_board_t s_sim_board;

bool sim_board_select(const char *name)
{
    if (strcmp(name, "waveshare-s3-175c") && strcmp(name, "watcher")) {
        return false;
    }
    s_sim_board = s_default_board;
    s_resolution = WATCHER_RESOLUTION;
    if (strcmp(name, "waveshare-s3-175c") == 0) {
        s_resolution = 466;
        s_sim_board.name = "Waveshare ESP32-S3-Touch-AMOLED-1.75C Simulator";
        s_sim_board.width = s_sim_board.height = s_resolution;
        s_sim_board.diagonal_in = 1.75f;
        s_sim_board.talk_button = "top";
        s_sim_board.aux_button = "bottom";
        s_sim_board.talk_hint = (muse_button_hint_t){ LV_ALIGN_CENTER, 153, -129 };
        s_sim_board.aux_hint = (muse_button_hint_t){ LV_ALIGN_CENTER, 153, 129 };
        s_sim_board.audio_init = sim_audio_init;
    }
    return true;
}

const muse_board_t *sim_board_get(void)
{
    return s_sim_board.name ? &s_sim_board : &s_default_board;
}

lv_display_t *sim_board_display(void)
{
    return s_display;
}
