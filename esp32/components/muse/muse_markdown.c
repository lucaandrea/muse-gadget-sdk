/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#include "muse_markdown.h"
#include "muse_text.h"
#include "vendor/md4c/md4c.h"
#include "vendor/md4c/entity.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    muse_markdown_t *doc;
    const char *source;
    size_t source_len, cursor;
    int strong, em, code, quote, link, strike, heading, depth;
    struct { unsigned number; bool ordered; } lists[32];
    bool head, failed;
    unsigned column;
    size_t cell_start;
    char headers[16][64];
} parse_t;

static uint8_t style(const parse_t *p)
{
    return ((p->strong || p->heading || p->head) ? MUSE_MD_BOLD : 0) |
        (p->em ? MUSE_MD_ITALIC : 0) | (p->code ? MUSE_MD_CODE : 0) |
        (p->quote ? MUSE_MD_QUOTE : 0) | (p->link ? MUSE_MD_LINK : 0) |
        (p->strike ? MUSE_MD_STRIKE : 0) | (p->heading ? MUSE_MD_HEADING : 0);
}

static uint32_t decode(const char *s, size_t remaining, size_t *bytes)
{
    const unsigned char *p = (const unsigned char *)s;
    *bytes = 1;
    if (*p < 128) return *p;
    unsigned n = *p >= 0xf0 && *p <= 0xf4 ? 4 : *p >= 0xe0 && *p < 0xf0 ? 3 : *p >= 0xc2 && *p < 0xe0 ? 2 : 1;
    if (n == 1 || n > remaining) return '?';
    uint32_t cp = *p & ((1u << (7-n))-1);
    for (unsigned i=1; i<n; ++i) {
        if ((p[i] & 0xc0) != 0x80) return '?';
        cp = (cp << 6) | (p[i] & 63);
    }
    if ((n==2 && cp<0x80) || (n==3 && cp<0x800) || (n==4 && cp<0x10000) ||
        cp>0x10ffff || (cp>=0xd800 && cp<=0xdfff)) return '?';
    *bytes = n;
    return cp;
}

static int put(parse_t *p, const char *s, size_t n, uint8_t flags, size_t origin, bool literal)
{
    muse_markdown_t *d = p->doc;
    while (n) {
        size_t bytes;
        uint32_t cp = decode(s, n, &bytes);
        const char *text = cp == '?' && (unsigned char)*s >= 128 && bytes == 1 ? "?" : s;
        if (d->length + bytes >= MUSE_MD_CAP) {
            d->text[d->length] = 0; p->failed = true; return 1;
        }
        memcpy(d->text + d->length, text, bytes);
        memset(d->style + d->length, flags, bytes);
        for (size_t i=0; i<bytes; ++i)
            d->origin[d->length+i] = (uint16_t)(origin > 65535 ? 65535 : origin);
        d->length += bytes;
        s += bytes; n -= bytes;
        if (literal) origin += bytes;
    }
    d->text[d->length] = 0;
    return 0;
}

static int newline(parse_t *p)
{
    if (!p->doc->length || p->doc->text[p->doc->length-1] == '\n') return 0;
    return put(p, "\n", 1, style(p), p->cursor, false);
}

static int enter_block(MD_BLOCKTYPE type, void *detail, void *user)
{
    parse_t *p = user;
    switch (type) {
    case MD_BLOCK_QUOTE: p->quote++; return newline(p);
    case MD_BLOCK_UL:
    case MD_BLOCK_OL:
        if (p->depth == 32) { p->failed = true; return 1; }
        p->lists[p->depth].ordered = type == MD_BLOCK_OL;
        p->lists[p->depth++].number = type == MD_BLOCK_OL ? ((MD_BLOCK_OL_DETAIL *)detail)->start : 0;
        return newline(p);
    case MD_BLOCK_LI: {
        if (newline(p)) return 1;
        for (int i=1; i<p->depth; ++i)
            if (put(p, "  ", 2, MUSE_MD_INDENT, p->cursor, false)) return 1;
        MD_BLOCK_LI_DETAIL *li = detail;
        char prefix[32];
        if (li->is_task) snprintf(prefix, sizeof(prefix), "%s ", li->task_mark == ' ' ? "[ ]" : "[x]");
        else if (p->depth && p->lists[p->depth-1].ordered)
            snprintf(prefix, sizeof(prefix), "%u. ", p->lists[p->depth-1].number++);
        else snprintf(prefix, sizeof(prefix), "\xe2\x80\xa2 ");
        return put(p, prefix, strlen(prefix), style(p), p->cursor, false);
    }
    case MD_BLOCK_H: p->heading++; return newline(p);
    case MD_BLOCK_CODE: p->code++; return newline(p);
    case MD_BLOCK_HR:
        if (newline(p) || put(p, "-----", 5, MUSE_MD_QUOTE, p->cursor, false)) return 1;
        return newline(p);
    case MD_BLOCK_P:
        /* A list prefix belongs to its first paragraph on the same line. */
        return 0;
    case MD_BLOCK_TABLE: memset(p->headers, 0, sizeof(p->headers)); return newline(p);
    case MD_BLOCK_THEAD: p->head = true; break;
    case MD_BLOCK_TR: p->column = 0; return newline(p);
    case MD_BLOCK_TH: p->cell_start = p->doc->length; break;
    case MD_BLOCK_TD:
        if (newline(p)) return 1;
        if (p->column < 16 && p->headers[p->column][0]) {
            if (put(p, p->headers[p->column], strlen(p->headers[p->column]), MUSE_MD_BOLD, p->cursor, false) ||
                put(p, ": ", 2, MUSE_MD_BOLD, p->cursor, false)) return 1;
        }
        break;
    default: break;
    }
    return 0;
}

static int leave_block(MD_BLOCKTYPE type, void *detail, void *user)
{
    (void)detail;
    parse_t *p = user;
    switch (type) {
    case MD_BLOCK_QUOTE: p->quote--; break;
    case MD_BLOCK_UL: case MD_BLOCK_OL: if (p->depth) p->depth--; break;
    case MD_BLOCK_H: p->heading--; break;
    case MD_BLOCK_CODE: p->code--; break;
    case MD_BLOCK_THEAD: p->head = false; break;
    case MD_BLOCK_TH:
        if (p->column < 16) {
            size_t n = p->doc->length - p->cell_start;
            if (n >= sizeof(p->headers[0])) n = sizeof(p->headers[0])-1;
            while (n && ((unsigned char)p->doc->text[p->cell_start+n] & 0xc0) == 0x80) n--;
            memcpy(p->headers[p->column], p->doc->text+p->cell_start, n);
            p->headers[p->column][n] = 0;
        }
        p->column++;
        return newline(p);
    case MD_BLOCK_TD: p->column++; return newline(p);
    case MD_BLOCK_DOC: return 0;
    default: break;
    }
    return newline(p);
}

static int span(MD_SPANTYPE type, void *detail, void *user, int delta)
{
    (void)detail;
    parse_t *p = user;
    switch (type) {
    case MD_SPAN_STRONG: p->strong += delta; break;
    case MD_SPAN_EM: p->em += delta; break;
    case MD_SPAN_CODE: p->code += delta; break;
    case MD_SPAN_A: p->link += delta; break;
    case MD_SPAN_DEL: p->strike += delta; break;
    case MD_SPAN_IMG:
        /* Alternative text remains readable; the device does not fetch images. */
        if (delta > 0) return put(p, "Image: ", 7, MUSE_MD_ITALIC, p->cursor, false);
        break;
    default: break;
    }
    return 0;
}
static int enter_span(MD_SPANTYPE t, void *d, void *p) { return span(t,d,p,1); }
static int leave_span(MD_SPANTYPE t, void *d, void *p) { return span(t,d,p,-1); }

static int codepoint(parse_t *p, uint32_t cp, size_t origin)
{
    char text[4]; int n;
    if (cp == 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) cp = 0xfffd;
    if (cp < 0x80) { text[0] = (char)cp; n=1; }
    else if (cp < 0x800) { text[0]=0xc0|(cp>>6); text[1]=0x80|(cp&63); n=2; }
    else if (cp < 0x10000) { text[0]=0xe0|(cp>>12); text[1]=0x80|((cp>>6)&63); text[2]=0x80|(cp&63); n=3; }
    else { text[0]=0xf0|(cp>>18); text[1]=0x80|((cp>>12)&63); text[2]=0x80|((cp>>6)&63); text[3]=0x80|(cp&63); n=4; }
    return put(p,text,(size_t)n,style(p),origin,false);
}

static int text(MD_TEXTTYPE type, const MD_CHAR *s, MD_SIZE n, void *user)
{
    parse_t *p = user;
    size_t origin = p->cursor;
    if ((uintptr_t)s >= (uintptr_t)p->source && (uintptr_t)s < (uintptr_t)p->source+p->source_len) {
        origin = (size_t)(s-p->source); p->cursor = origin+n;
    }
    if (type == MD_TEXT_SOFTBR) return put(p," ",1,style(p),origin,false);
    if (type == MD_TEXT_BR) return newline(p);
    if (type == MD_TEXT_NULLCHAR) return codepoint(p,0xfffd,origin);
    if (type == MD_TEXT_ENTITY) {
        if (n>3 && s[1]=='#') {
            char value[32]; size_t count = n-3;
            if (count < sizeof(value)) {
                memcpy(value,s+2,count); value[count]=0;
                bool hex = value[0]=='x' || value[0]=='X';
                return codepoint(p,(uint32_t)strtoul(value+(hex?1:0),NULL,hex?16:10),origin);
            }
        } else {
            const ENTITY *e = entity_lookup(s,n);
            if (e) {
                if (codepoint(p,e->codepoints[0],origin)) return 1;
                return e->codepoints[1] ? codepoint(p,e->codepoints[1],origin) : 0;
            }
        }
    }
    return put(p,s,n,style(p),origin,true);
}

bool muse_markdown_parse(muse_markdown_t *doc, const char *source)
{
    if (!doc) return false;
    doc->length = 0; doc->fallback = false; doc->text[0]=0;
    if (!source) return true;
    parse_t p = {.doc=doc, .source=source, .source_len=strlen(source)};
    MD_PARSER parser = { .flags=MD_DIALECT_GITHUB | MD_FLAG_NOHTML,
        .enter_block=enter_block, .leave_block=leave_block,
        .enter_span=enter_span, .leave_span=leave_span, .text=text };
    int result = md_parse(source, (MD_SIZE)p.source_len, &parser, &p);
    /* Keep our own failure flag: a table callback abort can be consumed by
     * MD4C 0.5.2's table processing before md_parse returns. */
    if (p.failed) result = 1;
    if (result) {
        /* Preserve the complete bounded reply if pathological markup exceeds
         * the expanded document/depth budget or the parser cannot allocate. */
        doc->length=0; doc->text[0]=0; doc->fallback=true;
        put(&p,source,p.source_len,0,0,true);
    }
    while (doc->length && doc->text[doc->length-1]=='\n') doc->text[--doc->length]=0;
    return result == 0;
}

static size_t line_end(const muse_markdown_t *d, size_t start, int width, muse_md_width_fn measure, void *ctx)
{
    size_t p=start, brk=start, previous=start;
    int used=0;
    muse_text_cjk_t prev_cjk=MUSE_TEXT_NOT_CJK;
    bool prev_open=false;
    while (p<d->length && d->text[p]!='\n') {
        size_t n; uint32_t cp=decode(d->text+p,d->length-p,&n);
        int w=measure(cp,d->style[p],ctx); if(w<1) w=1;
        muse_text_cjk_t cjk=muse_text_cjk(d->text+p);
        if(p>start && cjk!=MUSE_TEXT_CJK_CLOSE && !prev_open && (cjk||prev_cjk)) brk=p;
        if(used+w>width && p>start) {
            if(brk>start) return brk;
            if(cjk==MUSE_TEXT_CJK_CLOSE && previous>start) return previous;
            return p;
        }
        if(d->text[p]==' ') brk=p;
        used+=w; previous=p; prev_cjk=cjk;
        prev_open=cp=='(' || cp=='[' || cp==0x3008 || cp==0x300a || cp==0x300c || cp==0x300e || cp==0x3010 || cp==0xff08;
        p+=n;
    }
    return p;
}

static size_t skip(const muse_markdown_t *d, size_t p)
{
    while(p<d->length && (d->text[p]=='\n' || (d->text[p]==' ' && !(d->style[p]&(MUSE_MD_CODE|MUSE_MD_INDENT))))) p++;
    return p;
}

muse_reading_page_t muse_markdown_page(const muse_markdown_t *d, int width, int lines,
    size_t source_byte, int requested, muse_md_width_fn measure, void *ctx,
    char *out, uint8_t *styles, size_t cap)
{
    muse_reading_page_t r={.pages=1};
    if(cap) out[0]=0;
    if(!d || !d->length || !measure || width<1 || lines<1) return r;
    int count=0, follow=0;
    for(size_t p=skip(d,0); p<d->length;) {
        if(d->origin[p]<=source_byte) follow=count/lines;
        count++;
        size_t end=line_end(d,p,width,measure,ctx);
        p=skip(d,end<d->length && d->text[end]=='\n' ? end+1 : end);
    }
    r.pages=count ? (count+lines-1)/lines : 1;
    int wanted=requested<0 ? follow : requested;
    r.page=wanted<0 ? 0 : wanted>=r.pages ? r.pages-1 : wanted;
    size_t written=0; int line=0;
    for(size_t p=skip(d,0); p<d->length && line<(r.page+1)*lines; line++) {
        size_t end=line_end(d,p,width,measure,ctx);
        if(line>=r.page*lines) {
            if(line==r.page*lines) r.start=d->origin[p];
            r.end=end>p ? (size_t)d->origin[end-1]+1 : r.start;
            size_t n=end-p;
            if(written+n+(written?1:0)+1<=cap) {
                if(written) { if(styles) styles[written]=0; out[written++]='\n'; }
                memcpy(out+written,d->text+p,n);
                if(styles) memcpy(styles+written,d->style+p,n);
                written+=n; out[written]=0;
            }
        }
        p=skip(d,end<d->length && d->text[end]=='\n' ? end+1 : end);
    }
    return r;
}
