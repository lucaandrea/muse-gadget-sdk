/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#include "muse_reply.h"
#include "muse_text.h"
#include "muse_mem.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include <string.h>

static char *s_text;
static SemaphoreHandle_t s_lock;
static muse_reply_info_t s_info;

bool muse_reply_init(void)
{
    if (s_text && s_lock) return true;
    if (!s_lock) s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return false;
    s_text = heap_caps_calloc(1, MUSE_REPLY_MAX, MUSE_BIG_CAPS);
    return s_text && s_lock;
}

void muse_reply_new_turn(void)
{
    if (!s_text || !s_lock) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_text[0] = 0; s_info.length = s_info.offset = 0;
    s_info.turn++; s_info.revision++;
    xSemaphoreGive(s_lock);
}

void muse_reply_publish(const char *text, size_t at)
{
    if (!s_text || !s_lock || !text) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    size_t in = 0, out = 0, anchor = 0;
    bool changed = false;
    while (text[in]) {
        size_t n; char shown[4];
        int count = muse_text_ascii(text + in, &n, shown);
        const char *put = count < 0 ? text + in : shown;
        size_t take = count < 0 ? n : (size_t)count;
        if (!n || out + take >= MUSE_REPLY_MAX) break;
        if (in <= at) anchor = out;
        if (memcmp(s_text + out, put, take)) changed = true;
        memcpy(s_text + out, put, take);
        in += n; out += take;
    }
    if (out != s_info.length) changed = true;
    s_text[out] = 0; s_info.length = out; s_info.offset = anchor;
    if (changed) s_info.revision++;
    xSemaphoreGive(s_lock);
}

bool muse_reply_sync(char *out, size_t cap, muse_reply_info_t *info)
{
    if (!s_text || !s_lock || !info || !xSemaphoreTake(s_lock, 0)) return false;
    bool changed = info->revision != s_info.revision;
    if (changed && cap) {
        size_t n = s_info.length < cap - 1 ? s_info.length : cap - 1;
        memcpy(out, s_text, n); out[n] = 0;
    }
    *info = s_info;
    xSemaphoreGive(s_lock);
    return changed;
}
