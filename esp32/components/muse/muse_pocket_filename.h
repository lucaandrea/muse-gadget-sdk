/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* A 128-bit note ID needs 22 unpadded base64url characters. With /, .pcm,
 * .tmp and NUL, the longest SPIFFS name fits its existing 32-byte format.
 * Keep the full hexadecimal ID on the wire; changing the filesystem's name
 * length would change its on-flash layout and put queued data at risk. */
#define MUSE_POCKET_NOTE_FILENAME_SIZE 27
#define MUSE_POCKET_NOTE_SPIFFS_SIZE 32
static const char MUSE_POCKET_FILENAME_ALPHABET[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

static inline bool muse_pocket_note_filename(const char *id, char out[MUSE_POCKET_NOTE_FILENAME_SIZE])
{
    if (strlen(id) != 32) return false;
    uint32_t value = 0;
    unsigned bits = 0, n = 0;
    for (unsigned i = 0; i < 32; i++) {
        unsigned char c = (unsigned char)id[i];
        unsigned digit;
        if (c >= '0' && c <= '9') digit = c - '0';
        else if (c >= 'a' && c <= 'f') digit = c - 'a' + 10;
        else return false;
        value = (value << 4) | digit;
        bits += 4;
        if (bits >= 6) {
            bits -= 6;
            out[n++] = MUSE_POCKET_FILENAME_ALPHABET[(value >> bits) & 63];
        }
    }
    if (bits) out[n++] = MUSE_POCKET_FILENAME_ALPHABET[(value << (6 - bits)) & 63];
    memcpy(out + n, ".pcm", 5);
    return true;
}

static inline bool muse_pocket_note_id(const char *name, char out[33])
{
    if (strlen(name) != 26 || strcmp(name + 22, ".pcm")) return false;
    static const char hex[] = "0123456789abcdef";
    uint32_t value = 0;
    unsigned bits = 0, n = 0;
    for (unsigned i = 0; i < 22; i++) {
        const char *c = strchr(MUSE_POCKET_FILENAME_ALPHABET, name[i]);
        if (!c || !*c) return false;
        value = (value << 6) | (unsigned)(c - MUSE_POCKET_FILENAME_ALPHABET);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            unsigned byte = (value >> bits) & 255;
            out[n++] = hex[byte >> 4];
            out[n++] = hex[byte & 15];
        }
    }
    if (n != 32 || (value & 15)) return false; /* reject non-canonical padding */
    out[n] = '\0';
    return true;
}
