#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define MUSE_NOTEBOOK_HEADER_SIZE 64
#define MUSE_NOTEBOOK_MAX_SEGMENTS 240

typedef struct { char id[33]; uint16_t position; bool marked; uint32_t pcm_bytes; } muse_notebook_segment_t;
static inline uint32_t muse_notebook_checksum(const uint8_t *data)
{
    uint32_t value=2166136261u;
    for (int i=0;i<60;i++) value=(value^data[i])*16777619u;
    return value;
}
static inline bool muse_notebook_encode(uint8_t out[64], const muse_notebook_segment_t *segment)
{
    if (strlen(segment->id)!=32 || segment->position>=MUSE_NOTEBOOK_MAX_SEGMENTS || !segment->pcm_bytes || segment->pcm_bytes%4 || segment->pcm_bytes>480000) return false;
    for (int i=0;i<32;i++) if (!((segment->id[i]>='0' && segment->id[i]<='9') || (segment->id[i]>='a' && segment->id[i]<='f'))) return false;
    memset(out,0,64); memcpy(out,"MPC3ADP",7);
    for (int i=0;i<4;i++) out[8+i]=segment->pcm_bytes>>(i*8);
    out[12]=segment->position&255; out[13]=segment->position>>8; out[14]=segment->marked; out[15]=1;
    memcpy(out+16,segment->id,32);
    uint32_t checksum=muse_notebook_checksum(out);
    for (int i=0;i<4;i++) out[60+i]=checksum>>(i*8);
    return true;
}
/* 0 means another capture format; -1 means a damaged notebook header. */
static inline int muse_notebook_decode(const uint8_t *data,size_t available,size_t file_size,muse_notebook_segment_t *segment)
{
    if (available<7 || memcmp(data,"MPC3ADP",7)) return 0;
    if (available<64 || data[7] || data[14]>1 || data[15]!=1) return -1;
    memset(segment,0,sizeof(*segment));
    for (int i=0;i<4;i++) segment->pcm_bytes|=(uint32_t)data[8+i]<<(i*8);
    segment->position=(uint16_t)data[12]|((uint16_t)data[13]<<8); segment->marked=data[14]; memcpy(segment->id,data+16,32);
    uint8_t expected[64];
    if (!muse_notebook_encode(expected,segment) || memcmp(expected,data,64) || file_size!=64+segment->pcm_bytes/4) return -1;
    return 1;
}
