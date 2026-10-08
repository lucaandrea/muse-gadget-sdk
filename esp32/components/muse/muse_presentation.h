/* Copyright (c) Meta Platforms, Inc. and affiliates. SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "muse_state.h"
typedef struct {
    uint32_t turn;
    bool answer, dismissed, reading, manual;
    int page;
} muse_presentation_t;
/* True once when a fresh answer arrives. Idle/reconnect never replays it. */
bool muse_presentation_update(muse_presentation_t *p, uint32_t turn, bool has_reply);
void muse_presentation_dismiss(muse_presentation_t *p);
