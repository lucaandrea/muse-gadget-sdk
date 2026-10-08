/* Byte boundaries in an HTTP stream need not match PCM16 sample boundaries. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint8_t low;
    bool pending;
} muse_tts_pcm_t;

static inline size_t muse_tts_pcm_decode(muse_tts_pcm_t *s, const uint8_t *bytes, size_t len, int16_t *out)
{
    size_t n = 0;
    for (size_t i = 0; i < len; i++) {
        if (s->pending) {
            out[n++] = (int16_t)((uint16_t)s->low | (uint16_t)bytes[i] << 8);
        } else {
            s->low = bytes[i];
        }
        s->pending = !s->pending;
    }
    return n;
}

/* A request stays below the API's 4096-character limit, without cutting UTF-8.
 * Prefer a word boundary. The next request resumes at exactly this byte. */
static inline size_t muse_tts_text_chunk(const char *text, size_t len)
{
    if (len <= 4095) {
        return len;
    }
    size_t n = 4095;
    while (((uint8_t)text[n] & 0xc0) == 0x80) {
        n--;
    }
    for (size_t i = n; i > n / 2; i--) {
        if (text[i - 1] == ' ' || text[i - 1] == '\n') {
            return i;
        }
    }
    return n;
}
