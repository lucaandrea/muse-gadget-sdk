/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#include "muse_theme.h"
#include "sdkconfig.h"
#include <string.h>

void muse_surface(lv_obj_t *obj, bool paper, int radius)
{
    lv_obj_set_style_radius(obj, radius, 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(obj, lv_color_hex(paper ? MUSE_CREAM : MUSE_INK), 0);
    lv_obj_set_style_border_width(obj, 1, 0);
    lv_obj_set_style_border_color(obj, lv_color_hex(paper ? MUSE_PAPER : 0x49433d), 0);
    lv_obj_set_style_outline_width(obj, 2, 0);
    lv_obj_set_style_outline_pad(obj, 0, 0);
    lv_obj_set_style_outline_color(obj, lv_color_hex(MUSE_EDGE), 0);
    lv_obj_set_style_shadow_width(obj, 0, 0);
    lv_obj_set_style_bg_color(obj, lv_color_hex(paper ? 0xe9e0d4 : 0x302a24), LV_STATE_PRESSED);
}

lv_obj_t *muse_label(lv_obj_t *parent, const lv_font_t *font, uint32_t color, const char *text)
{
    lv_obj_t *obj = lv_label_create(parent);
    lv_obj_set_style_text_font(obj, font, 0);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_text_line_space(obj, 3, 0);
    lv_label_set_text(obj, text);
    return obj;
}

void muse_label_update(lv_obj_t *label, const char *text)
{
    if (strcmp(lv_label_get_text(label), text)) lv_label_set_text(label, text);
}

const lv_font_t *muse_body_font(void)
{
#if CONFIG_MUSE_CJK_FONT
    LV_FONT_DECLARE(muse_font_cjk_16);
    static lv_font_t font;
    if (!font.get_glyph_dsc) {
        font = lv_font_montserrat_20;
        font.fallback = &muse_font_cjk_16;
    }
    return &font;
#else
    return &lv_font_montserrat_20;
#endif
}
