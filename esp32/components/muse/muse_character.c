#include "muse_character.h"
#include "muse_mem.h"
#include "esp_heap_caps.h"
#include "src/misc/cache/instance/lv_image_cache.h"
#include <math.h>
#include <string.h>

extern const uint16_t muse_character_palette[256];
extern const uint32_t muse_character_offsets[15];
extern const uint8_t muse_character_rle[];
#define SIDE 224
static uint8_t *s_indices;
static uint16_t *s_pixels;
static lv_obj_t *s_image;
static lv_image_dsc_t s_source;
static int s_pose = -1, s_size, s_orb_frame = -1;
static float s_points[96][3];

lv_obj_t *muse_character_create(lv_obj_t *parent)
{
    s_indices = heap_caps_malloc(SIDE * SIDE, MUSE_BIG_CAPS);
    s_pixels = heap_caps_malloc(SIDE * SIDE * 2, MUSE_BIG_CAPS);
    if (!s_indices || !s_pixels) {
        heap_caps_free(s_indices); heap_caps_free(s_pixels);
        s_indices = NULL; s_pixels = NULL;
        return NULL;
    }
    for (int i = 0; i < 96; ++i) {
        float y = 1.f - 2.f * (i + .5f) / 96;
        float r = sqrtf(1.f - y*y), a = i * 2.39996323f;
        s_points[i][0] = cosf(a)*r; s_points[i][1] = y; s_points[i][2] = sinf(a)*r;
    }
    s_source.header.magic = LV_IMAGE_HEADER_MAGIC;
    s_source.header.cf = LV_COLOR_FORMAT_RGB565;
    s_source.data = (const uint8_t *)s_pixels;
    s_image = lv_image_create(parent);
    lv_obj_remove_flag(s_image, LV_OBJ_FLAG_SCROLLABLE);
    return s_image;
}

static void dot(int x, int y, int r, int size, uint16_t color)
{
    for (int j = -r; j <= r; j++) for (int i = -r; i <= r; i++) {
        if (i*i+j*j <= r*r && x+i >= 0 && y+j >= 0 && x+i < size && y+j < size)
            s_pixels[(y+j)*size+x+i] = color;
    }
}

void muse_character_update(muse_mode_t mode, float now, float level, bool happy,
                           bool quiet, bool reduced, int size)
{
    if (!s_image) return;
    size = LV_CLAMP(32, size, SIDE);
    int pose = 0;
    if (happy) pose = 6;
    else if (mode == MUSE_MODE_LISTENING) pose = 2;
    else if (mode == MUSE_MODE_THINKING) pose = 3;
    else if (mode == MUSE_MODE_SPEAKING && !reduced) pose = level > .36f ? 5 : level > .09f ? 4 : 0;
    else if (!reduced && fmodf(now, 5.2f) < .16f) pose = 1;
    int frame = reduced ? 0 : (int)(now * (mode == MUSE_MODE_IDLE ? 12 : 20));
    if (quiet) {
        if (s_pose == -2 && s_size == size && s_orb_frame == frame) return;
        memset(s_pixels, 0, size*size*2);
        float angle = reduced ? .4f : now * (mode == MUSE_MODE_IDLE ? .28f : .48f);
        float c = cosf(angle), sn = sinf(angle);
        for (int i = 0; i < 96; ++i) {
            float x = s_points[i][0]*c + s_points[i][2]*sn;
            float z = s_points[i][2]*c - s_points[i][0]*sn;
            float y = s_points[i][1];
            int shade = (int)(90 + (z+1)*70);
            uint16_t color = ((shade>>3)<<11) | ((shade>>2)<<5) | (shade>>3);
            if (z > .2f && fabsf(y - sinf(angle*2)*.65f) < .2f) color = 0xf9e0;
            dot(size/2 + x*size*.34f, size/2 + y*size*.34f,
                LV_MAX(1, (int)(size * (.009f + (z+1)*.002f))), size, color);
        }
        s_pose = -2; s_orb_frame = frame;
    } else {
        int native = size <= 96 ? 96 : SIDE;
        int index = pose + (native == 96 ? 7 : 0);
        if (s_pose == index && s_size == size) return;
        if (s_pose != index) {
            size_t at = 0;
            for (uint32_t p = muse_character_offsets[index]; p < muse_character_offsets[index+1]; p += 2) {
                unsigned n = muse_character_rle[p];
                if (at+n > (size_t)(native*native)) return;
                memset(s_indices+at, muse_character_rle[p+1], n); at += n;
            }
        }
        for (int y = 0; y < size; y++) for (int x = 0; x < size; x++)
            s_pixels[y*size+x] = muse_character_palette[s_indices[(y*native/size)*native+x*native/size]];
        s_pose = index;
    }
    s_size = size;
    lv_image_cache_drop(&s_source);
    s_source.header.w = size; s_source.header.h = size; s_source.header.stride = size*2;
    s_source.data_size = size*size*2;
    lv_image_set_src(s_image, &s_source);
    lv_obj_invalidate(s_image);
}
