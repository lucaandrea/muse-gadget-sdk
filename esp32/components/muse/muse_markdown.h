/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "muse_reading.h"

#define MUSE_MD_CAP 32768
enum {
    MUSE_MD_BOLD = 1, MUSE_MD_ITALIC = 2, MUSE_MD_CODE = 4,
    MUSE_MD_QUOTE = 8, MUSE_MD_LINK = 16, MUSE_MD_STRIKE = 32,
    MUSE_MD_HEADING = 64, MUSE_MD_INDENT = 128
};
/* Bounded document storage, allocated in PSRAM by the UI. Origins preserve
 * voice following and manual anchors after Markdown punctuation is removed. */
typedef struct {
    size_t length;
    bool fallback;
    char text[MUSE_MD_CAP];
    uint8_t style[MUSE_MD_CAP];
    uint16_t origin[MUSE_MD_CAP];
} muse_markdown_t;
typedef int (*muse_md_width_fn)(uint32_t cp, uint8_t style, void *context);
bool muse_markdown_parse(muse_markdown_t *doc, const char *source);
muse_reading_page_t muse_markdown_page(const muse_markdown_t *doc, int width, int lines,
    size_t source_byte, int requested_page, muse_md_width_fn measure, void *context,
    char *out, uint8_t *styles, size_t capacity);
