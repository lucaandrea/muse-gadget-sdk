#pragma once
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

void muse_pocket_init(void);
void muse_pocket_start(void);
bool muse_pocket_enabled(void);
bool muse_pocket_busy(void);
bool muse_pocket_audio_pending(void);
void muse_pocket_play_chunk(void);
void muse_pocket_record(QueueHandle_t input);
void muse_pocket_speech_test(void);
bool muse_pocket_action(const char *id, const char *action);
/* Handles credentials before the generic console logger; never prints them. */
bool muse_pocket_console(char *line, bool whole);
