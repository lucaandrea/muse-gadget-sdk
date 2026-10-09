#include "muse_cards.h"
#include "muse_pocket.h"
#include "muse_mem.h"
#include "muse_state.h"
#include "muse_theme.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include <stdio.h>
#include <string.h>

#define CARD_COUNT 16
static muse_card_t *s_cards;
static SemaphoreHandle_t s_lock;
static int s_count, s_selected;
static unsigned s_version, s_drawn;
static lv_obj_t *s_panel, *s_inbox, *s_title, *s_body, *s_source, *s_counter, *s_buttons[3];
static muse_card_action_fn s_action;
static bool s_visible, s_refined;
static lv_obj_t *s_progress, *s_steps, *s_scroll, *s_phone;
#if LV_USE_QRCODE
static lv_obj_t *s_qr_panel, *s_qr;
#endif
static void phone_cb(lv_event_t *e)
{
    (void)e;
#if LV_USE_QRCODE
    char url[321] = {0};
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_selected < s_count) snprintf(url,sizeof(url),"%s",s_cards[s_selected].handoff);
    xSemaphoreGive(s_lock);
    if (strncmp(url,"https://",8) && strncmp(url,"http://localhost",16)) return;
    if (lv_qrcode_update(s_qr,url,strlen(url))==LV_RESULT_OK) {
        lv_obj_remove_flag(s_qr_panel,LV_OBJ_FLAG_HIDDEN); lv_obj_move_foreground(s_qr_panel);
    }
#else
    muse_state_set_caption("Open this item in Muse Companion on your phone");
#endif
}
#if LV_USE_QRCODE
static void qr_close_cb(lv_event_t *e) { (void)e; lv_obj_add_flag(s_qr_panel,LV_OBJ_FLAG_HIDDEN); }
#endif

bool muse_cards_init(void)
{
    if (s_cards) return true;
    s_cards = heap_caps_calloc(CARD_COUNT, sizeof(*s_cards), MUSE_BIG_CAPS);
    s_lock = xSemaphoreCreateMutex();
    return s_cards && s_lock;
}
void muse_cards_submit(const muse_card_t *card)
{
    if (!s_cards || !card->id[0]) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    int i;
    for (i = 0; i < s_count; ++i) if (!strcmp(s_cards[i].id, card->id)) break;
    if (i == CARD_COUNT) { memmove(s_cards, s_cards + 1, sizeof(*s_cards) * (CARD_COUNT - 1)); i--; }
    else if (i == s_count) s_count++;
    s_cards[i] = *card;
    s_version++;
    xSemaphoreGive(s_lock);
}
void muse_cards_remove(const char *id)
{
    if (!s_cards) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    for (int i = 0; i < s_count; ++i) if (!strcmp(s_cards[i].id, id)) {
        memmove(s_cards + i, s_cards + i + 1, sizeof(*s_cards) * (--s_count - i));
        s_version++; break;
    }
    xSemaphoreGive(s_lock);
}
void muse_cards_show(bool show)
{
    if (!s_panel) return;
    s_visible = show;
    lv_obj_set_flag(s_panel, LV_OBJ_FLAG_HIDDEN, !show);
    if (show) {
        xSemaphoreTake(s_lock, portMAX_DELAY); s_selected = s_count ? s_count - 1 : 0; xSemaphoreGive(s_lock);
        lv_obj_move_foreground(s_panel); s_drawn = ~s_version; muse_state_poke();
    }
}
void muse_cards_clear(void)
{
    if (!s_cards) return;
    xSemaphoreTake(s_lock, portMAX_DELAY); s_count=0; s_version++; xSemaphoreGive(s_lock);
}
void muse_cards_invalidate_memories(void)
{
    if (!s_cards) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    for (int i=0; i<s_count;) {
        if (!strcmp(s_cards[i].kind,"memory") || !strcmp(s_cards[i].kind,"answer")) {
            memmove(s_cards+i,s_cards+i+1,sizeof(*s_cards)*(--s_count-i)); s_version++;
        } else i++;
    }
    xSemaphoreGive(s_lock);
}
static void open_cb(lv_event_t *e) { (void)e; muse_cards_show(true); }
static void close_cb(lv_event_t *e) { (void)e; muse_cards_show(false); }
static void next_cb(lv_event_t *e) { (void)e; s_selected++; s_drawn = ~s_version; muse_state_poke(); }
static void action_cb(lv_event_t *e)
{
    int index = (int)(intptr_t)lv_event_get_user_data(e);
    char id[65] = {0}, action[16] = {0};
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_selected < s_count) {
        snprintf(id, sizeof(id), "%s", s_cards[s_selected].id);
        snprintf(action, sizeof(action), "%s", s_cards[s_selected].buttons[index].action);
    }
    xSemaphoreGive(s_lock);
    if (!id[0]) return;
    if (!strcmp(action, "open") || !strcmp(action, "next")) {
        phone_cb(e);
    } else if (!strcmp(action, "dismiss")) muse_cards_remove(id);
    else if (s_action && s_action(id, action)) lv_label_set_text(s_source, "Processing… check saved status");
    else lv_label_set_text(s_source, "Offline. Try again when connected.");
    muse_state_poke();
}
static lv_obj_t *button(lv_obj_t *parent, const char *text, lv_event_cb_t callback, void *data)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_remove_style_all(b);
    muse_surface(b, false, 18);
    lv_obj_remove_flag(b, LV_OBJ_FLAG_PRESS_LOCK | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_pad_all(b, 8, 0);
    lv_obj_add_event_cb(b, callback, LV_EVENT_CLICKED, data);
    lv_obj_t *label = muse_label(b, &lv_font_montserrat_14, MUSE_CREAM, text); lv_obj_center(label);
    return b;
}
void muse_cards_chrome_visible(bool visible)
{
    if (s_inbox) lv_obj_set_flag(s_inbox, LV_OBJ_FLAG_HIDDEN, !visible);
}
void muse_cards_build(lv_obj_t *parent, int width, int height, muse_card_action_fn action, bool refined)
{
    if (!muse_cards_init()) return;
    s_action = action; s_refined = refined;
    s_inbox = button(parent, "Inbox", open_cb, NULL);
    if (refined) {
        /* Opposite the touch speaker, clear of the title and the small reply
         * character. Scale the entire hit target into the circular safe area. */
        int scale = LV_MIN(width, height);
        lv_obj_set_size(s_inbox, 52 * scale / 466, 52 * scale / 466);
        lv_obj_set_pos(s_inbox, (width-scale)/2 + 326 * scale / 466,
                       (height-scale)/2 + 72 * scale / 466);
    } else {
        lv_obj_set_size(s_inbox, 90, 38); lv_obj_align(s_inbox, LV_ALIGN_TOP_MID, 0, 78);
    }
    s_panel = lv_obj_create(lv_screen_active());
    lv_obj_remove_flag(s_panel, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_size(s_panel, width, height); lv_obj_center(s_panel);
    lv_obj_set_style_bg_color(s_panel, lv_color_black(), 0);
    lv_obj_set_style_border_width(s_panel, 0, 0);
    lv_obj_set_style_pad_all(s_panel, 0, 0);
    lv_obj_set_style_text_color(s_panel, lv_color_hex(MUSE_CREAM), 0);
    lv_obj_set_style_text_font(s_panel, muse_body_font(), 0);
    lv_obj_t *close = button(s_panel, "Back", close_cb, NULL);
    lv_obj_set_size(close, 78, 38); lv_obj_align(close, LV_ALIGN_TOP_MID, -90, 44);
    lv_obj_t *next = button(s_panel, "Next", next_cb, NULL);
    lv_obj_set_size(next, 78, 38); lv_obj_align(next, LV_ALIGN_TOP_MID, 90, 44);
    s_phone = button(s_panel,"Phone",phone_cb,NULL);
    lv_obj_set_size(s_phone,78,38); lv_obj_align(s_phone,LV_ALIGN_TOP_MID,0,44);
    s_counter = lv_label_create(s_panel); lv_obj_align(s_counter, LV_ALIGN_TOP_MID, 0, 91);
    s_title = lv_label_create(s_panel); lv_obj_set_width(s_title, width * 70 / 100);
    lv_obj_set_height(s_title, 44); lv_label_set_long_mode(s_title, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(s_title, LV_TEXT_ALIGN_CENTER, 0); lv_obj_align(s_title, LV_ALIGN_TOP_MID, 0, 115);
    lv_obj_t *scroll = lv_obj_create(s_panel);
    /* Keep scrolling text above the progress/source footer, including when
     * the source wraps or the body is longer than the visible viewport. */
    lv_obj_set_size(scroll, width * 75 / 100, LV_MAX(30, height - 335)); lv_obj_align(scroll, LV_ALIGN_TOP_MID, 0, 165);
    lv_obj_set_style_bg_opa(scroll, LV_OPA_TRANSP, 0); lv_obj_set_style_border_width(scroll, 0, 0);
    lv_obj_set_style_pad_all(scroll, 2, 0);
    s_scroll = scroll;
    lv_obj_set_flex_flow(scroll,LV_FLEX_FLOW_COLUMN);
    s_steps = lv_label_create(scroll); lv_obj_set_width(s_steps,LV_PCT(100));
    lv_obj_set_style_text_color(s_steps,lv_color_hex(MUSE_MUTED),0);
    s_body = lv_label_create(scroll); lv_obj_set_width(s_body, LV_PCT(100));
    lv_obj_set_style_text_color(s_body, lv_color_hex(MUSE_CREAM), 0);
    s_source = lv_label_create(s_panel); lv_obj_set_width(s_source, width * 70 / 100);
    lv_obj_set_height(s_source,20);
    lv_obj_set_style_text_font(s_source,&lv_font_montserrat_14,0);
    lv_label_set_long_mode(s_source, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_color(s_source, lv_color_hex(MUSE_MUTED), 0);
    lv_obj_set_style_text_align(s_source, LV_TEXT_ALIGN_CENTER, 0); lv_obj_align(s_source, LV_ALIGN_BOTTOM_MID, 0, -127);
    for (int i = 0; i < 3; ++i) {
        s_buttons[i] = button(s_panel, "", action_cb, (void *)(intptr_t)i);
        lv_obj_set_size(s_buttons[i], width / 5, 44);
        lv_obj_align(s_buttons[i], LV_ALIGN_BOTTOM_MID, (i - 1) * (width / 5 + 6), -72);
    }
    s_progress = lv_bar_create(s_panel);
    lv_obj_set_size(s_progress,width*60/100,5); lv_obj_align(s_progress,LV_ALIGN_BOTTOM_MID,0,-150);
    lv_bar_set_range(s_progress,0,100);
#if LV_USE_QRCODE
    s_qr_panel=lv_obj_create(s_panel); lv_obj_set_size(s_qr_panel,width*80/100,height*75/100);
    lv_obj_center(s_qr_panel); lv_obj_remove_flag(s_qr_panel,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(s_qr_panel,lv_color_black(),0); lv_obj_set_style_bg_opa(s_qr_panel,LV_OPA_COVER,0);
    s_qr=lv_qrcode_create(s_qr_panel); lv_qrcode_set_size(s_qr,LV_MIN(width,height)/2);
    lv_qrcode_set_quiet_zone(s_qr,true); lv_qrcode_set_dark_color(s_qr,lv_color_black()); lv_qrcode_set_light_color(s_qr,lv_color_white());
    lv_obj_align(s_qr,LV_ALIGN_CENTER,0,-25);
    lv_obj_t *qr_close=button(s_qr_panel,"Back to card",qr_close_cb,NULL); lv_obj_align(qr_close,LV_ALIGN_BOTTOM_MID,0,0);
    lv_obj_add_flag(s_qr_panel,LV_OBJ_FLAG_HIDDEN);
#endif
    muse_cards_show(false);
    s_drawn = ~s_version;
}
void muse_cards_tick(void)
{
    if (s_visible && muse_state_mode(NULL) == MUSE_MODE_LISTENING && !muse_pocket_notebook_recording()) muse_cards_show(false);
    if (!s_panel || s_drawn == s_version) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    char counter[40]; snprintf(counter, sizeof(counter), s_refined ? LV_SYMBOL_LIST " %d" : "Inbox %d", s_count);
    lv_label_set_text(lv_obj_get_child(s_inbox, 0), counter);
    if (s_visible) {
        if (s_selected >= s_count) s_selected = 0;
        muse_card_t *card = s_count ? &s_cards[s_selected] : NULL;
        snprintf(counter, sizeof(counter), "%d / %d", s_count ? s_selected + 1 : 0, s_count);
        lv_label_set_text(s_counter, counter);
        lv_label_set_text(s_title, card ? card->title : "All caught up");
        lv_label_set_text(s_body, card ? card->body : "Hold the talk button to capture a thought or ask for help.");
        lv_label_set_text(s_source, card ? card->source : "Muse Companion");
        lv_obj_set_flag(s_phone,LV_OBJ_FLAG_HIDDEN,!card || !card->handoff[0]);
        lv_obj_set_flag(s_progress,LV_OBJ_FLAG_HIDDEN,!card || !card->has_progress);
        if (card && card->has_progress) lv_bar_set_value(s_progress,card->progress,LV_ANIM_OFF);
        char steps[300]={0};
        if (card) for (int i=0;i<3 && card->steps[i][0];i++) {
            size_t used=strlen(steps); snprintf(steps+used,sizeof(steps)-used,"%s%s",i?"\n  " LV_SYMBOL_DOWN "\n":"",card->steps[i]);
        }
        lv_label_set_text(s_steps,steps); lv_obj_set_flag(s_steps,LV_OBJ_FLAG_HIDDEN,!steps[0]);
        (void)s_scroll;
        for (int i = 0; i < 3; ++i) {
            lv_obj_set_flag(s_buttons[i], LV_OBJ_FLAG_HIDDEN, !card || !card->buttons[i].action[0]);
            if (card) lv_label_set_text(lv_obj_get_child(s_buttons[i], 0), card->buttons[i].label);
        }
    }
    s_drawn = s_version;
    xSemaphoreGive(s_lock);
}
void muse_cards_demo(void)
{
    muse_card_t card = {.id="demo-reminder", .kind="reminder", .title="Bring the prototype", .body="Your meeting starts in 30 minutes. Take Muse and the USB-C cable.", .source="Reminder / today, 10:00", .status="due", .buttons={{"Done","done"},{"Snooze","snooze"},{"Dismiss","dismiss"}}};
    muse_cards_submit(&card); muse_cards_show(true); muse_cards_tick();
}
bool muse_cards_bench_action(const char *name)
{
#if LV_USE_SNAPSHOT
    if (!s_visible || !s_panel) return false;
    if (!strcmp(name,"next")) { next_cb(NULL); muse_cards_tick(); return true; }
    if (!strcmp(name,"phone") && !lv_obj_has_flag(s_phone,LV_OBJ_FLAG_HIDDEN)) {
        phone_cb(NULL); return true;
    }
    if (!strcmp(name,"back")) { close_cb(NULL); return true; }
    if (name[0]>='0' && name[0]<='2' && !name[1]) {
        lv_obj_t *control=s_buttons[name[0]-'0'];
        if (!lv_obj_has_flag(control,LV_OBJ_FLAG_HIDDEN)) {
            lv_obj_send_event(control,LV_EVENT_CLICKED,NULL); return true;
        }
    }
#else
    (void)name;
#endif
    return false;
}
