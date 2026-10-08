/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define MUSE_REPLY_MAX 16384
typedef struct { uint32_t revision, turn; size_t offset, length; } muse_reply_info_t;
bool muse_reply_init(void);
void muse_reply_new_turn(void);
/* Called by the voice/text producer; never uses LVGL. Retained after playback. */
void muse_reply_publish(const char *text, size_t byte);
/* UI task copies only when the text changes. A busy producer never stalls it. */
bool muse_reply_sync(char *out, size_t capacity, muse_reply_info_t *info);
#ifdef __cplusplus
}
#endif
