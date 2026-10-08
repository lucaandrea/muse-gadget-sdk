/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#include "muse_presentation.h"
bool muse_presentation_update(muse_presentation_t *p, uint32_t turn, bool has_reply)
{
    if (turn != p->turn) *p = (muse_presentation_t){ .turn = turn };
    bool reveal = has_reply && !p->answer && !p->dismissed;
    if (reveal) p->answer = true;
    return reveal;
}
void muse_presentation_dismiss(muse_presentation_t *p)
{
    p->answer = p->reading = p->manual = false; p->dismissed = true;
}
