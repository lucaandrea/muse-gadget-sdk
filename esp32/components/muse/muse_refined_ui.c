#include "muse_refined_ui.h"
#include "muse_character.h"
#include "muse_theme.h"
#include "muse_reply.h"
#include "muse_reading.h"
#include "muse_presentation.h"
#include "muse_settings.h"
#include "muse_wifi.h"
#include "muse_chat.h"
#include "muse_board.h"
#include "muse_mem.h"
#include "esp_heap_caps.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static lv_obj_t *s_character, *s_title, *s_hint, *s_hint_text, *s_foot, *s_speaker, *s_speaker_icon;
static lv_obj_t *s_card, *s_body, *s_card_title, *s_page_label, *s_follow, *s_follow_text;
static lv_obj_t *s_nav, *s_prev, *s_next, *s_bars[7], *s_activity, *s_notice;
static int s_w, s_h, s_layout = -1, s_last_size, s_last_y, s_last_bars[7];
static float s_scale, s_reveal, s_next_text;
static float s_move_at, s_last_tick = -1;
static int s_target_size = -1, s_target_y, s_from_size, s_from_y;
static char *s_text;
static char s_caption[MUSE_CAPTION_MAX];
static uint32_t s_caption_version;
static muse_reply_info_t s_reply;
static muse_presentation_t s_view;
static muse_reading_page_t s_page;
static size_t s_manual_byte;
static int s_body_width, s_lines;
static bool s_hold;
static int px(int n) { return (int)(n*s_scale + .5f); }
static void hidden(lv_obj_t *o, bool hide) { lv_obj_set_flag(o, LV_OBJ_FLAG_HIDDEN, hide); }
static void place(lv_obj_t *o, int x, int y, int w, int h)
{
    lv_obj_set_size(o, px(w), px(h));
    lv_obj_set_pos(o, (s_w-px(466))/2+px(x), (s_h-px(466))/2+px(y));
}
static int glyph(uint32_t cp, void *font) { return lv_font_get_glyph_width(font, cp, 0); }

static void on_pet(lv_event_t *e) { (void)e; muse_state_make_happy(); muse_state_poke(); }
static void on_speaker(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_PRESSED) { s_hold = false; muse_state_poke(); }
    if (code == LV_EVENT_PRESS_LOST) s_hold = true;
    if (code == LV_EVENT_CLICKED && !s_hold) muse_settings_set_speaker_on(!muse_settings_speaker_on());
}
static void on_read(lv_event_t *e)
{
    (void)e;
    if (!s_view.answer || s_view.reading) return;
    s_view.reading = true; s_view.manual = true; s_manual_byte = s_page.start;
    s_layout = -1; s_next_text = 0; muse_state_poke();
}
static void on_done(lv_event_t *e)
{
    (void)e;
    if (s_view.answer) muse_presentation_dismiss(&s_view);
    muse_state_poke();
}
static void on_back(lv_event_t *e)
{
    (void)e; s_view.reading = false; s_layout = -1; s_next_text = 0; muse_state_poke();
}
static void on_follow(lv_event_t *e)
{
    (void)e; s_view.manual = false; s_next_text = 0; muse_state_poke();
}
static void on_page(lv_event_t *e)
{
    int delta = (int)(intptr_t)lv_event_get_user_data(e);
    char page[1024];
    muse_reading_page_t p = muse_reading_page(s_text, s_body_width, s_lines, 0,
        s_page.page + delta, glyph, (void *)muse_body_font(), page, sizeof(page));
    s_view.manual = true; s_manual_byte = p.start; s_next_text = 0; muse_state_poke();
}
static lv_obj_t *control(lv_obj_t *p, const char *text, int w, lv_event_cb_t cb, void *user)
{
    lv_obj_t *b = lv_button_create(p); lv_obj_remove_style_all(b);
    muse_surface(b, false, 24); lv_obj_set_size(b, px(w), px(52));
    lv_obj_set_style_opa(b, LV_OPA_40, LV_STATE_DISABLED);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, user);
    lv_obj_t *l = muse_label(b, &lv_font_montserrat_16, MUSE_CREAM, text); lv_obj_center(l);
    return b;
}

bool muse_refined_build(lv_obj_t *parent, int width, int height)
{
    s_w = width; s_h = height; s_scale = LV_MIN(width, height)/466.f;
    s_text = heap_caps_calloc(1, MUSE_REPLY_MAX, MUSE_BIG_CAPS);
    if (!s_text || !muse_reply_init()) { heap_caps_free(s_text); return false; }
    s_character = muse_character_create(parent);
    if (!s_character) { heap_caps_free(s_text); return false; }
    lv_obj_add_flag(s_character, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_character, on_pet, LV_EVENT_CLICKED, NULL);
    s_title = muse_label(parent, &lv_font_montserrat_20, MUSE_CREAM, "Muse");
    lv_obj_align(s_title, LV_ALIGN_TOP_MID, 0, px(46));
    s_speaker = lv_button_create(parent); lv_obj_remove_style_all(s_speaker);
    muse_surface(s_speaker, false, 26); place(s_speaker, 88, 72, 52, 52);
    lv_obj_remove_flag(s_speaker, LV_OBJ_FLAG_PRESS_LOCK | LV_OBJ_FLAG_SCROLLABLE);
    s_speaker_icon = muse_label(s_speaker, &lv_font_montserrat_20, MUSE_CREAM, LV_SYMBOL_VOLUME_MAX);
    lv_obj_center(s_speaker_icon);
    lv_obj_add_event_cb(s_speaker, on_speaker, LV_EVENT_ALL, NULL);
    s_hint = control(parent, "", 256, on_done, NULL); place(s_hint, 105, 354, 256, 52);
    s_hint_text = lv_obj_get_child(s_hint, 0);
    s_foot = muse_label(parent, &lv_font_montserrat_14, MUSE_MUTED, "Swipe left for settings");
    lv_obj_align(s_foot, LV_ALIGN_TOP_MID, 0, px(417));
    s_notice = muse_label(parent, &lv_font_montserrat_16, MUSE_MUTED, "");
    lv_obj_set_width(s_notice, px(270)); lv_obj_set_style_text_align(s_notice, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_notice, LV_ALIGN_TOP_MID, 0, px(278)); lv_obj_set_height(s_notice, px(66));
    lv_label_set_long_mode(s_notice, LV_LABEL_LONG_MODE_DOTS);
    s_activity = lv_obj_create(parent); lv_obj_remove_style_all(s_activity);
    place(s_activity, 184, 315, 98, 26); lv_obj_remove_flag(s_activity, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    for (int i=0; i<7; ++i) {
        s_bars[i] = lv_obj_create(s_activity); lv_obj_remove_style_all(s_bars[i]);
        lv_obj_set_style_bg_color(s_bars[i], lv_color_hex(MUSE_ORANGE), 0);
        lv_obj_set_style_bg_opa(s_bars[i], LV_OPA_COVER, 0);
        lv_obj_set_style_radius(s_bars[i], 4, 0);
        lv_obj_remove_flag(s_bars[i], LV_OBJ_FLAG_CLICKABLE);
        s_last_bars[i] = -1;
    }
    s_card = lv_obj_create(parent); lv_obj_remove_style_all(s_card);
    muse_surface(s_card, true, 24); lv_obj_remove_flag(s_card, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_card, on_read, LV_EVENT_CLICKED, NULL);
    s_card_title = muse_label(s_card, &lv_font_montserrat_14, MUSE_PAPER_MUTED, "FROM MUSE");
    lv_obj_set_pos(s_card_title, px(20), px(17));
    s_body = muse_label(s_card, muse_body_font(), MUSE_INK, "");
    lv_obj_set_style_text_line_space(s_body, 3, 0);
    lv_obj_set_pos(s_body, px(20), px(46));
    s_page_label = muse_label(s_card, &lv_font_montserrat_14, MUSE_PAPER_MUTED, "");
    lv_obj_align(s_page_label, LV_ALIGN_BOTTOM_LEFT, px(20), -px(14));
    s_follow = lv_button_create(s_card); lv_obj_remove_style_all(s_follow);
    lv_obj_set_size(s_follow, px(100), px(44));
    lv_obj_align(s_follow, LV_ALIGN_TOP_RIGHT, -px(8), px(2));
    lv_obj_add_event_cb(s_follow, on_follow, LV_EVENT_CLICKED, NULL);
    s_follow_text = muse_label(s_follow, &lv_font_montserrat_14, MUSE_INK, "Follow voice"); lv_obj_center(s_follow_text);
    s_nav = lv_obj_create(parent); lv_obj_remove_style_all(s_nav);
    place(s_nav, 105, 370, 256, 52); lv_obj_remove_flag(s_nav, LV_OBJ_FLAG_SCROLLABLE);
    s_prev = control(s_nav, LV_SYMBOL_LEFT, 64, on_page, (void *)(intptr_t)-1);
    lv_obj_t *back = control(s_nav, "Back", 96, on_back, NULL); lv_obj_set_x(back, px(80));
    s_next = control(s_nav, LV_SYMBOL_RIGHT, 64, on_page, (void *)(intptr_t)1); lv_obj_set_x(s_next, px(192));
    hidden(s_card, true); hidden(s_nav, true); hidden(s_notice, true); hidden(s_activity, true);
    muse_state_set_page(24, 4);
    return true;
}

static void layout(bool answer, bool reading)
{
    int state = reading ? 2 : answer ? 1 : 0;
    if (state == s_layout) return;
    s_layout = state; s_next_text = 0;
    hidden(s_card, !answer); hidden(s_hint, reading); hidden(s_nav, !reading);
    hidden(s_foot, reading); hidden(s_follow, !reading);
    hidden(s_speaker, reading);
    if (answer) {
        place(s_card, reading ? 65 : 73, reading ? 103 : 184, reading ? 336 : 320, reading ? 251 : 158);
        s_body_width = px(reading ? 296 : 280);
        int height = px(reading ? 168 : 78);
        s_lines = LV_MAX(1, height / (lv_font_get_line_height(muse_body_font()) + 3));
        lv_obj_set_width(s_body, s_body_width); lv_obj_set_height(s_body, height);
    }
}

void muse_refined_tick(muse_mode_t mode, float now, float level)
{
    bool reduced = muse_settings_reduced_motion(), quiet = !muse_settings_character();
    bool returning = s_last_tick >= 0 && now - s_last_tick > 1;
    s_last_tick = now;
    muse_state_caption(s_caption, sizeof(s_caption), &s_caption_version);
    muse_reply_sync(s_text, MUSE_REPLY_MAX, &s_reply);
    if (muse_presentation_update(&s_view, s_reply.turn, s_reply.length > 0)) s_reveal = now;
    bool answer = s_view.answer, reading = answer && s_view.reading;
    layout(answer, reading);
    static const char *const titles[] = { "Waking up", "Muse", "Listening", "Thinking", "Speaking", "Let's try again", "See you soon" };
    muse_label_update(s_title, reading ? "Your reply" : answer && mode == MUSE_MODE_THINKING ? "Muse" : titles[mode]);
    muse_label_update(s_speaker_icon, muse_settings_speaker_on() ? LV_SYMBOL_VOLUME_MAX : LV_SYMBOL_MUTE);
    lv_obj_set_style_text_color(s_speaker_icon, lv_color_hex(muse_settings_speaker_on() ? MUSE_CREAM : MUSE_ORANGE), 0);
    muse_wifi_status_t wifi; muse_wifi_status(&wifi);
    muse_hatch_status_t chat; muse_hatch_status(&chat);
    bool setup = chat.state == MUSE_HATCH_NOT_SET;
    bool offline = wifi.state != MUSE_WIFI_CONNECTED;
    bool active = mode == MUSE_MODE_LISTENING || mode == MUSE_MODE_THINKING;
    bool notice = !reading && (mode == MUSE_MODE_ERROR || (!answer && (setup || offline)));
    lv_obj_align(s_notice, LV_ALIGN_TOP_MID, 0, px(answer ? 103 : 278));
    hidden(s_notice, !notice); hidden(s_activity, !active || answer || notice);
    char hint[64];
    if (mode == MUSE_MODE_LISTENING) snprintf(hint, sizeof(hint), "Release to send");
    else if (mode == MUSE_MODE_THINKING) snprintf(hint, sizeof(hint), "A moment, please");
    else if (answer) snprintf(hint, sizeof(hint), "Done");
    else if (setup) snprintf(hint, sizeof(hint), "Connect in the Muse app");
    else if (offline) snprintf(hint, sizeof(hint), "Swipe left for Wi-Fi");
    else snprintf(hint, sizeof(hint), "Hold %s to talk", muse_board->talk_button);
    muse_label_update(s_hint_text, hint);
    if (notice) muse_label_update(s_notice, mode == MUSE_MODE_ERROR && s_caption[0] ? s_caption :
        setup ? "Let's get acquainted.\nPair Muse with your phone." :
        wifi.state == MUSE_WIFI_CONNECTING ? "Reconnecting to Wi-Fi..." : "You're offline.\nCheck your Wi-Fi in Settings.");
    muse_power_t power = muse_state_power();
    muse_label_update(s_foot, power.battery_pct >= 0 && power.battery_pct <= 15 && !power.charging ? "Low battery - Connect power" : "Swipe left for settings");
    float reveal = reduced ? 1 : LV_CLAMP(0.f, (now-s_reveal)/.28f, 1.f);
    float eased = 1 - (1-reveal)*(1-reveal)*(1-reveal);
    int target_size = px(reading ? 96 : answer ? 96 : notice ? 156 : 224);
    int target_y = px(answer ? 79 : notice ? 110 : 108);
    if (target_size != s_target_size || target_y != s_target_y || returning) {
        s_from_size = s_last_size && !returning ? s_last_size : target_size - px(8);
        s_from_y = s_last_size && !returning ? s_last_y : target_y + px(8);
        s_target_size = target_size; s_target_y = target_y; s_move_at = now;
    }
    float move = reduced ? 1 : LV_CLAMP(0.f, (now - s_move_at)/.28f, 1.f);
    float settle = 1 - (1-move)*(1-move)*(1-move);
    int size = s_from_size + (int)((s_target_size-s_from_size)*settle);
    int y = s_from_y + (int)((s_target_y-s_from_y)*settle);
    if (!reduced && move >= 1 && !answer && !notice && mode == MUSE_MODE_IDLE) y += (int)(sinf(now*1.4f)*2);
    hidden(s_character, reading || (answer && notice));
    if (!reading && !(answer && notice)) {
        muse_character_update(mode, now, level, muse_state_happiness() > .1f, quiet, reduced, size);
        if (size != s_last_size || y != s_last_y) { lv_obj_align(s_character, LV_ALIGN_TOP_MID, 0, y); s_last_size = size; s_last_y = y; }
    }
    if (answer && !reading) lv_obj_set_y(s_card, (s_h-px(466))/2+px(184)+(int)(px(10)*(1-eased)));
    if (active && !answer && !notice) for (int i=0; i<7; ++i) {
        int height = mode == MUSE_MODE_LISTENING ? 4 + (int)(level * (12+8*sinf(now*8+i))) :
            5 + (reduced ? 0 : (int)(4+4*sinf(now*3-i*.65f)));
        if (reduced) height = 7;
        if (height != s_last_bars[i]) {
            lv_obj_set_size(s_bars[i], px(7), px(height)); lv_obj_set_pos(s_bars[i], px(i*14), px((26-height)/2));
            s_last_bars[i] = height;
        }
    }
    if (answer && now >= s_next_text) {
        s_next_text = now + .1f;
        char body[1024], footer[80];
        s_page = muse_reading_page(s_text, s_body_width, s_lines,
            s_view.manual ? s_manual_byte : s_reply.offset, -1, glyph, (void *)muse_body_font(), body, sizeof(body));
        muse_label_update(s_body, body);
        snprintf(footer, sizeof(footer), reading ? "%d / %d" : "%d / %d    Tap to read", s_page.page+1, s_page.pages);
        muse_label_update(s_page_label, footer);
        muse_label_update(s_follow_text, s_view.manual ? "Follow voice" : "Following");
        lv_obj_set_state(s_prev, LV_STATE_DISABLED, s_page.page == 0);
        lv_obj_set_state(s_next, LV_STATE_DISABLED, s_page.page+1 >= s_page.pages);
    }
}

bool muse_refined_preview(const char *name)
{
    if (!strcmp(name, "reading")) { s_view.reading = true; s_view.manual = true; s_manual_byte = s_page.start; }
    else if (!strcmp(name, "quiet")) { muse_settings_set_character(false); s_view.reading = false; }
    else if (!strcmp(name, "companion")) { muse_settings_set_character(true); s_view.reading = false; }
    else if (strcmp(name, "face")) return false;
    s_layout = -1; s_next_text = 0; return true;
}

#if LV_USE_SNAPSHOT
bool muse_refined_check(const char *property, int expected)
{
    if (!strcmp(property, "answer")) return s_view.answer == expected;
    if (!strcmp(property, "reading")) return s_view.reading == expected;
    if (!strcmp(property, "manual")) return s_view.manual == expected;
    if (!strcmp(property, "page")) return s_page.page == expected;
    return false;
}
#endif
