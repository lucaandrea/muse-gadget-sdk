#include "muse_cards.h"
#include "muse_mem.h"
#include "muse_state.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include <stdio.h>
#include <string.h>

#define CARD_COUNT 8
static muse_card_t *s_cards;
static SemaphoreHandle_t s_lock;
static int s_count, s_selected;
static unsigned s_version, s_drawn;
static lv_obj_t *s_panel, *s_inbox, *s_title, *s_body, *s_source, *s_counter, *s_buttons[3];
static muse_card_action_fn s_action;
static bool s_visible;

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
        muse_state_set_caption("Open the companion for the full report");
    } else if (!strcmp(action, "dismiss")) muse_cards_remove(id);
    else if (s_action && s_action(id, action)) lv_label_set_text(s_source, "Sending... awaiting confirmation");
    else lv_label_set_text(s_source, "Offline. Try again when connected.");
    muse_state_poke();
}
static lv_obj_t *button(lv_obj_t *parent, const char *text, lv_event_cb_t callback, void *data)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_style_bg_color(b, lv_color_hex(0x214A40), 0);
    lv_obj_set_style_radius(b, 18, 0);
    lv_obj_set_style_pad_all(b, 8, 0);
    lv_obj_add_event_cb(b, callback, LV_EVENT_CLICKED, data);
    lv_obj_t *label = lv_label_create(b); lv_label_set_text(label, text); lv_obj_center(label);
    return b;
}
void muse_cards_build(lv_obj_t *parent, int width, int height, muse_card_action_fn action)
{
    if (!muse_cards_init()) return;
    s_action = action;
    s_inbox = button(parent, "Inbox", open_cb, NULL);
    lv_obj_set_size(s_inbox, 90, 38); lv_obj_align(s_inbox, LV_ALIGN_TOP_MID, 0, 48);
    s_panel = lv_obj_create(lv_screen_active());
    lv_obj_remove_flag(s_panel, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_size(s_panel, width, height); lv_obj_center(s_panel);
    lv_obj_set_style_bg_color(s_panel, lv_color_black(), 0);
    lv_obj_set_style_border_width(s_panel, 0, 0);
    lv_obj_set_style_pad_all(s_panel, 0, 0);
    lv_obj_set_style_text_color(s_panel, lv_color_hex(0xF0F8F5), 0);
    lv_obj_t *close = button(s_panel, "Back", close_cb, NULL);
    lv_obj_set_size(close, 78, 38); lv_obj_align(close, LV_ALIGN_TOP_MID, -65, 44);
    lv_obj_t *next = button(s_panel, "Next", next_cb, NULL);
    lv_obj_set_size(next, 78, 38); lv_obj_align(next, LV_ALIGN_TOP_MID, 65, 44);
    s_counter = lv_label_create(s_panel); lv_obj_align(s_counter, LV_ALIGN_TOP_MID, 0, 91);
    s_title = lv_label_create(s_panel); lv_obj_set_width(s_title, width * 70 / 100);
    lv_obj_set_style_text_align(s_title, LV_TEXT_ALIGN_CENTER, 0); lv_obj_align(s_title, LV_ALIGN_TOP_MID, 0, 115);
    lv_obj_t *scroll = lv_obj_create(s_panel);
    lv_obj_set_size(scroll, width * 75 / 100, height - 309); lv_obj_align(scroll, LV_ALIGN_TOP_MID, 0, 165);
    lv_obj_set_style_bg_opa(scroll, LV_OPA_TRANSP, 0); lv_obj_set_style_border_width(scroll, 0, 0);
    lv_obj_set_style_pad_all(scroll, 2, 0);
    s_body = lv_label_create(scroll); lv_obj_set_width(s_body, LV_PCT(100));
    lv_obj_set_style_text_color(s_body, lv_color_hex(0xE2ECE7), 0);
    s_source = lv_label_create(s_panel); lv_obj_set_width(s_source, width * 70 / 100);
    lv_label_set_long_mode(s_source, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(s_source, LV_TEXT_ALIGN_CENTER, 0); lv_obj_align(s_source, LV_ALIGN_BOTTOM_MID, 0, -111);
    for (int i = 0; i < 3; ++i) {
        s_buttons[i] = button(s_panel, "", action_cb, (void *)(intptr_t)i);
        lv_obj_set_size(s_buttons[i], width / 5, 44);
        lv_obj_align(s_buttons[i], LV_ALIGN_BOTTOM_MID, (i - 1) * (width / 5 + 6), -55);
    }
    muse_cards_show(false);
}
void muse_cards_tick(void)
{
    if (s_visible && muse_state_mode(NULL) == MUSE_MODE_LISTENING) muse_cards_show(false);
    if (!s_panel || s_drawn == s_version) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    char counter[40]; snprintf(counter, sizeof(counter), "Inbox %d", s_count);
    lv_label_set_text(lv_obj_get_child(s_inbox, 0), counter);
    if (s_visible) {
        if (s_selected >= s_count) s_selected = 0;
        muse_card_t *card = s_count ? &s_cards[s_selected] : NULL;
        snprintf(counter, sizeof(counter), "%d / %d", s_count ? s_selected + 1 : 0, s_count);
        lv_label_set_text(s_counter, counter);
        lv_label_set_text(s_title, card ? card->title : "All caught up");
        lv_label_set_text(s_body, card ? card->body : "Hold the talk button to capture a thought or ask for help.");
        lv_label_set_text(s_source, card ? card->source : "Muse Companion");
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
