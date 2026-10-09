#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* Versioned metadata travels with offline PCM. Never reinterpret a translation
 * recording as an assistant command just because the current mode changed. */
#define MUSE_RECORDING_HEADER_SIZE 16
#define MUSE_RECORDING_MAX_PCM 480000

static inline bool muse_recording_language(const char *language)
{
    return language && (!language[0] || (language[0] >= 'a' && language[0] <= 'z' &&
           language[1] >= 'a' && language[1] <= 'z' && language[2] == 0));
}

static inline bool muse_recording_encode(uint8_t out[16], const char *language, size_t size)
{
    if (!muse_recording_language(language) || !size || size % 2 || size > MUSE_RECORDING_MAX_PCM) return false;
    memset(out, 0, 16); memcpy(out, "MPC2PCM", 7);
    out[8] = language[0] ? 1 : 0;
    if (out[8]) { out[9] = language[0]; out[10] = language[1]; }
    out[11] = 0xa5 ^ out[8] ^ out[9] ^ out[10];
    for (int i = 0; i < 4; i++) out[12+i] = (uint32_t)size >> (8*i);
    return true;
}

/* 0: legacy raw PCM; 1: validated header; -1: corrupt versioned recording. */
static inline int muse_recording_decode(const uint8_t *data, size_t available,
                                       size_t file_size, char language[3])
{
    language[0] = language[1] = language[2] = 0;
    if (available < 7 || memcmp(data, "MPC2PCM", 7)) return 0;
    if (available < 16 || data[7] || data[8] > 1 || data[11] != (0xa5 ^ data[8] ^ data[9] ^ data[10])) return -1;
    uint32_t size = 0;
    for (int i = 0; i < 4; i++) size |= (uint32_t)data[12+i] << (8*i);
    if (!size || size % 2 || size > MUSE_RECORDING_MAX_PCM || file_size != size + 16) return -1;
    if (data[8]) {
        language[0] = data[9]; language[1] = data[10];
        if (!language[0] || !muse_recording_language(language)) return -1;
    } else if (data[9] || data[10]) return -1;
    return 1;
}
