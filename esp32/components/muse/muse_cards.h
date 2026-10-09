#pragma once
#include <stdbool.h>
#include "lvgl.h"

typedef struct {
    char id[65], kind[24], title[81], body[1601], source[201], status[33];
    struct { char label[25], action[16]; } buttons[3];
} muse_card_t;
typedef bool (*muse_card_action_fn)(const char *id, const char *action);

/* submit/remove may run on the network task; all LVGL work stays on its task. */
bool muse_cards_init(void);
void muse_cards_build(lv_obj_t *parent, int width, int height, muse_card_action_fn action, bool refined);
void muse_cards_chrome_visible(bool visible);
void muse_cards_submit(const muse_card_t *card);
void muse_cards_remove(const char *id);
void muse_cards_clear(void);
void muse_cards_invalidate_memories(void);
void muse_cards_tick(void);
void muse_cards_show(bool show);
void muse_cards_demo(void);
