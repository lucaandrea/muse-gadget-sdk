/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "lvgl.h"

#define MUSE_ORANGE 0xff3c00
#define MUSE_CREAM 0xfaf6f1
#define MUSE_PAPER 0xfffcf8
#define MUSE_INK 0x181818
#define MUSE_MUTED 0xb6aea5
#define MUSE_PAPER_MUTED 0x655d54
#define MUSE_EDGE 0x37332e
#define MUSE_DANGER 0xe55d4e

/* Flat fills, hairlines and a short lower edge: no large blurred layers. */
void muse_surface(lv_obj_t *obj, bool paper, int radius);
lv_obj_t *muse_label(lv_obj_t *parent, const lv_font_t *font, uint32_t color, const char *text);
void muse_label_update(lv_obj_t *label, const char *text);
const lv_font_t *muse_body_font(void);
