/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef int (*muse_glyph_width_fn)(uint32_t codepoint, void *context);
typedef struct { int page, pages; size_t start, end; } muse_reading_page_t;

/* Pure UTF-8 pagination. requested_page < 0 follows the byte offset. */
muse_reading_page_t muse_reading_page(const char *text, int width, int lines,
    size_t byte, int requested_page, muse_glyph_width_fn measure, void *context,
    char *out, size_t capacity);
