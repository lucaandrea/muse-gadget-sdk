/* OpenAI speech for PSRAM Muse boards. Credentials live in NVS, never in source. */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MUSE_TTS_MODEL "gpt-4o-mini-tts"
#define MUSE_TTS_VOICE "coral"
#define MUSE_TTS_RATE 24000
#define MUSE_TTS_FRAMES 512
#define MUSE_TTS_KEY_MAX 512

/* USB provisioning only. Empty removes the key. Neither API returns the key. */
esp_err_t muse_tts_set_key(const char *key);
bool muse_tts_configured(void);

/* Session task only: one outstanding job. Zero means unavailable. Starting a
 * job cancels the previous one. Network I/O runs on a separate PSRAM task. */
uint32_t muse_tts_begin(const char *text);
void muse_tts_cancel(void);
/* Nonblocking: 1..512 frames of PCM24, 0 waiting, -1 complete, -2 failed.
 * Caller provides room for MUSE_TTS_FRAMES samples. Stale jobs are discarded. */
int muse_tts_read(uint32_t job, int16_t *pcm);

#ifdef __cplusplus
}
#endif
