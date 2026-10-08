/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#include "muse_reading.h"
#include "muse_text.h"
#include <stdbool.h>
#include <string.h>

static uint32_t decode(const char *s, size_t *length)
{
    const unsigned char *p = (const unsigned char *)s;
    uint32_t c = p[0];
    *length = 1;
    if (c < 0x80) return c;
    int n = c >= 0xf0 ? 4 : c >= 0xe0 ? 3 : c >= 0xc2 ? 2 : 1;
    if (n == 1) return '?';
    c &= (1u << (7 - n)) - 1;
    for (int i = 1; i < n; i++) {
        if (!p[i] || (p[i] & 0xc0) != 0x80) return '?';
        c = (c << 6) | (p[i] & 0x3f);
    }
    *length = (size_t)n;
    return c;
}

static const char *line_end(const char *s, int width, muse_glyph_width_fn measure, void *ctx)
{
    const char *p = s, *brk = NULL, *previous = s;
    int used = 0;
    muse_text_cjk_t prev_cjk = MUSE_TEXT_NOT_CJK;
    bool prev_open = false;
    while (*p && *p != '\n') {
        size_t n;
        uint32_t cp = decode(p, &n);
        int w = measure(cp, ctx);
        if (w < 1) w = 1;
        muse_text_cjk_t cjk = muse_text_cjk(p);
        if (p > s && cjk != MUSE_TEXT_CJK_CLOSE && !prev_open && (cjk || prev_cjk)) brk = p;
        if (used + w > width && p > s) {
            if (brk && brk > s) return brk;
            if (cjk == MUSE_TEXT_CJK_CLOSE && previous > s) return previous;
            return p;
        }
        if (*p == ' ') brk = p;
        used += w;
        previous = p;
        prev_cjk = cjk;
        prev_open = cp == '(' || cp == '[' || cp == 0x3008 || cp == 0x300a ||
                    cp == 0x300c || cp == 0x300e || cp == 0x3010 || cp == 0xff08;
        p += n;
    }
    return p;
}

muse_reading_page_t muse_reading_page(const char *text, int width, int lines,
    size_t byte, int requested_page, muse_glyph_width_fn measure, void *ctx,
    char *out, size_t cap)
{
    muse_reading_page_t r = { .pages = 1 };
    if (cap) out[0] = 0;
    if (!text || !*text || !measure || width < 1 || lines < 1) return r;
    int count = 0, wanted = requested_page, follow = 0;
    const char *p = text;
    /* Count first: no fixed line table, so arbitrary newlines cannot overflow it. */
    while (*p) {
        while (*p == ' ' || *p == '\n') p++;
        if (!*p) break;
        const char *end = line_end(p, width, measure, ctx);
        if ((size_t)(p - text) <= byte) follow = count / lines;
        count++;
        p = end;
    }
    r.pages = count ? (count + lines - 1) / lines : 1;
    if (wanted < 0) wanted = follow;
    r.page = wanted < 0 ? 0 : wanted >= r.pages ? r.pages - 1 : wanted;
    p = text;
    size_t written = 0;
    int line = 0;
    while (*p && line < (r.page + 1) * lines) {
        while (*p == ' ' || *p == '\n') p++;
        if (!*p) break;
        const char *end = line_end(p, width, measure, ctx);
        if (line >= r.page * lines) {
            if (line == r.page * lines) r.start = (size_t)(p - text);
            r.end = (size_t)(end - text);
            size_t n = (size_t)(end - p);
            if (written + n + (written ? 1 : 0) + 1 <= cap) {
                if (written) out[written++] = '\n';
                memcpy(out + written, p, n); written += n; out[written] = 0;
            }
        }
        line++; p = end;
    }
    return r;
}
