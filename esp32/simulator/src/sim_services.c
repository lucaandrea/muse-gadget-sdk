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

#include "sim_services.h"

#include <stdio.h>
#include <string.h>

#include "muse_console.h"
#include "muse_menu.h"
#include "muse_settings.h"
#include "muse_settings_ui.h"
#include "muse_battery.h"
#include "muse_voice.h"
#include "esp_app_desc.h"
#include "esp_mac.h"

#define SIM_DEFAULT_NAME "MuseGadget-SIM001"
#define SIM_DEFAULT_SSID "Muse Simulator"

static muse_wifi_status_t s_wifi = {
    .state = MUSE_WIFI_CONNECTED,
    .ssid = SIM_DEFAULT_SSID,
    .ip = "192.0.2.2",
    .rssi = -45,
};
static muse_ble_status_t s_ble = {
    .state = MUSE_BLE_CONNECTED,
    .secure = true,
    .name = SIM_DEFAULT_NAME,
};
static muse_hatch_status_t s_chat = {
    .state = MUSE_HATCH_REACHABLE,
};
static muse_link_state_t s_link = MUSE_LINK_ONLINE;
static int s_brightness = 75;
static bool s_speaker = true;
static bool s_character = true, s_reduced_motion;
bool muse_settings_character(void) { return s_character; }
bool muse_settings_reduced_motion(void) { return s_reduced_motion; }
void muse_settings_set_character(bool on) { s_character = on; }
void muse_settings_set_reduced_motion(bool on) { s_reduced_motion = on; }

static void copy_text(char *out, size_t cap, const char *text)
{
    if (!cap) {
        return;
    }
    if (!text) {
        text = "";
    }
    size_t n = strlen(text);
    if (n >= cap) {
        n = cap - 1;
    }
    memcpy(out, text, n);
    out[n] = '\0';
}

void sim_services_reset(void)
{
    s_wifi = (muse_wifi_status_t){
        .state = MUSE_WIFI_CONNECTED,
        .ssid = SIM_DEFAULT_SSID,
        .ip = "192.0.2.2",
        .rssi = -45,
    };
    s_ble = (muse_ble_status_t){
        .state = MUSE_BLE_CONNECTED,
        .secure = true,
        .name = SIM_DEFAULT_NAME,
    };
    s_chat = (muse_hatch_status_t){
        .state = MUSE_HATCH_REACHABLE,
    };
    s_link = MUSE_LINK_ONLINE;
    s_brightness = 75;
    s_speaker = true;
}

void sim_services_set_wifi(muse_wifi_state_t state, const char *ssid)
{
    s_wifi.state = state;
    copy_text(s_wifi.ssid, sizeof(s_wifi.ssid), ssid);
    copy_text(s_wifi.ip, sizeof(s_wifi.ip), state == MUSE_WIFI_CONNECTED ? "192.0.2.2" : "");
    s_wifi.rssi = state == MUSE_WIFI_CONNECTED ? -45 : 0;
    s_wifi.detail[0] = '\0';
}

void sim_services_set_ble(muse_ble_state_t state, const char *name, uint32_t passkey)
{
    s_ble.state = state;
    s_ble.passkey = passkey;
    s_ble.secure = state == MUSE_BLE_CONNECTED;
    copy_text(s_ble.name, sizeof(s_ble.name), name);
}

void sim_services_set_paired(bool paired)
{
    if (!paired) {
        s_chat.state = MUSE_HATCH_NOT_SET;
        s_chat.detail[0] = '\0';
    } else if (s_chat.state == MUSE_HATCH_NOT_SET) {
        s_chat.state = MUSE_HATCH_REACHABLE;
    }
}

void sim_services_set_chat_status(muse_hatch_state_t state, const char *detail)
{
    s_chat.state = state;
    copy_text(s_chat.detail, sizeof(s_chat.detail), detail);
}

void sim_services_set_link_state(muse_link_state_t state)
{
    s_link = state;
}

void sim_services_set_brightness(int pct)
{
    if (pct < 10) {
        pct = 10;
    } else if (pct > 100) {
        pct = 100;
    }
    s_brightness = pct;
}

void sim_services_set_speaker(bool on)
{
    s_speaker = on;
}

int muse_settings_brightness(void)
{
    return s_brightness;
}

bool muse_settings_speaker_on(void)
{
    return s_speaker;
}

void muse_settings_set_brightness(int pct)
{
    sim_services_set_brightness(pct);
}

void muse_settings_set_speaker_on(bool on)
{
    sim_services_set_speaker(on);
}

void muse_wifi_status(muse_wifi_status_t *out)
{
    if (out) {
        *out = s_wifi;
    }
}

bool muse_wifi_connected(void)
{
    return s_wifi.state == MUSE_WIFI_CONNECTED;
}

void muse_ble_status(muse_ble_status_t *out)
{
    if (out) {
        *out = s_ble;
    }
}

void muse_hatch_status(muse_hatch_status_t *out)
{
    if (out) {
        *out = s_chat;
    }
}

muse_link_state_t muse_link_state(void)
{
    return s_link;
}

/* Real settings views exercise the same navigation and controls as firmware.
 * Only the services beneath them are simulated. */
static int s_volume = 60, s_gain = 30, s_sleep = 120;
int muse_settings_volume(void) { return s_volume; }
int muse_settings_mic_gain(void) { return s_gain; }
int muse_settings_sleep_s(void) { return s_sleep; }
bool muse_settings_wifi_on(void) { return s_wifi.state != MUSE_WIFI_OFF; }
bool muse_settings_ble_on(void) { return s_ble.state != MUSE_BLE_OFF; }
void muse_settings_set_volume(int v) { s_volume = v; }
void muse_settings_set_mic_gain(int v) { s_gain = v; }
void muse_settings_set_sleep_s(int v) { s_sleep = v; }
void muse_settings_set_wifi_on(bool on) { s_wifi.state = on ? MUSE_WIFI_CONNECTED : MUSE_WIFI_OFF; }
void muse_settings_set_ble_on(bool on) { s_ble.state = on ? MUSE_BLE_CONNECTED : MUSE_BLE_OFF; }
void muse_settings_set_wifi(const char *ssid, const char *pass) { (void)pass; sim_services_set_wifi(MUSE_WIFI_CONNECTED, ssid); }
void muse_settings_hatch_host(char *out) { strcpy(out, "Muse simulator"); }
void muse_settings_hatch_vm(char *out) { strcpy(out, "Desktop preview"); }
size_t muse_settings_hatch_token_len(void) { return s_chat.state != MUSE_HATCH_NOT_SET ? 32 : 0; }
void muse_settings_set_hatch_host(const char *v) { (void)v; }
void muse_settings_set_hatch_vm(const char *v) { (void)v; }
esp_err_t muse_settings_set_hatch_token(const char *v, bool append) { (void)append; sim_services_set_paired(v && *v); return ESP_OK; }
void muse_audio_set_volume(int v) { s_volume = v; }
void muse_audio_set_mic_gain(int v) { s_gain = v; }
void muse_voice_set_monitor(bool on) { (void)on; }
float muse_voice_monitor_db(void) { return -36; }
void muse_voice_request_chirp(void) {}
void muse_voice_request_speechtest(void) {}
void muse_input_request_power_off(void) { muse_state_set_mode(MUSE_MODE_OFF); }
void muse_ble_forget_all(void) { s_ble.state = MUSE_BLE_OFF; }
void muse_hatch_test(void) { s_chat.state = MUSE_HATCH_REACHABLE; }
const char *muse_hatch_state_name(muse_hatch_state_t state) { return state == MUSE_HATCH_NOT_SET ? "Not paired" : "Connected"; }
const char *muse_link_state_name(muse_link_state_t state) { return state == MUSE_LINK_ONLINE ? "Connected" : "Offline"; }
bool muse_link_hatch_linked(void) { return s_chat.state != MUSE_HATCH_NOT_SET; }
void muse_link_reset_setup(void) { sim_services_set_paired(false); }
void muse_wifi_apply(void) {}
esp_err_t muse_wifi_scan(void) { return ESP_OK; }
bool muse_wifi_scanning(void) { return false; }
int muse_wifi_scan_results(muse_wifi_ap_t *out, int max, uint32_t *gen)
{
    *gen = 1;
    if (max < 1) return 0;
    *out = (muse_wifi_ap_t){ .ssid = SIM_DEFAULT_SSID, .rssi = -45, .secure = true }; return 1;
}
int muse_wifi_saved(muse_wifi_saved_t *out, int max)
{
    if (max < 1) return 0;
    *out = (muse_wifi_saved_t){ .ssid = SIM_DEFAULT_SSID }; return 1;
}
void muse_wifi_forget(const char *ssid) { (void)ssid; }
void muse_battery_read(muse_battery_t *out) { memset(out, 0, sizeof(*out)); }
void muse_battery_reset(void) {}
bool muse_battery_drain(const muse_battery_t *b, int *rate, int *hours) { (void)b; (void)rate; (void)hours; return false; }
const esp_app_desc_t *esp_app_get_description(void) { static const esp_app_desc_t desc = { .version = "preview" }; return &desc; }
esp_err_t esp_read_mac(uint8_t *mac, int type) { (void)type; memcpy(mac, "\x02\x00\x00\x00\x00\x01", 6); return ESP_OK; }

void muse_menu_key(muse_menu_key_t key)
{
    (void)key;
}

bool muse_menu_is_open(void)
{
    return false;
}

void muse_menu_build(lv_obj_t *parent, int w, int h)
{
    (void)parent;
    (void)w;
    (void)h;
}

bool muse_menu_tick(float now)
{
    (void)now;
    return false;
}

void muse_menu_close(void)
{
}

void muse_console_write(const void *buf, size_t n)
{
    if (buf && n) {
        (void)fwrite(buf, 1, n, stdout);
        (void)fflush(stdout);
    }
}
