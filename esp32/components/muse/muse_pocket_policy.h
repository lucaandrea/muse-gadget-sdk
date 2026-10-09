#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum { POCKET_RESPONSIVE, POCKET_BALANCED, POCKET_TRAVEL } pocket_profile_t;
typedef struct { int64_t asleep_since, wake_at, window_until; bool sleeping; } pocket_power_policy_t;

static inline const char *pocket_profile_name(pocket_profile_t profile)
{
    return profile == POCKET_RESPONSIVE ? "Responsive" : profile == POCKET_TRAVEL ? "Travel" : "Balanced";
}

/* All times are monotonic milliseconds. Waking the screen or attaching USB
 * resets the cycle. Background connection windows leave the display dark. */
static inline bool pocket_should_nap(pocket_power_policy_t *state, pocket_profile_t profile,
                                     int64_t now, bool asleep_on_battery, bool busy, bool force)
{
    if (!asleep_on_battery || profile == POCKET_RESPONSIVE) {
        *state = (pocket_power_policy_t){0};
        return false;
    }
    if (!state->asleep_since) state->asleep_since = now ? now : 1;
    if (busy) {
        state->sleeping = false;
        state->window_until = now + 45000;
        return false;
    }
    if (state->sleeping && now >= state->wake_at) {
        state->sleeping = false;
        state->window_until = now + 45000;
        return false;
    }
    if (state->sleeping) return true;
    if (now < state->window_until) return false;
    int64_t delay = profile == POCKET_TRAVEL ? 30000 : 120000;
    if (!force && now - state->asleep_since < delay) return false;
    state->sleeping = true;
    state->wake_at = now + (profile == POCKET_TRAVEL ? 1800000 : 300000);
    return true;
}

static inline bool pocket_compatible(int protocol, int schema, bool storage, bool receipts,
                                     unsigned internal_free, unsigned largest_block, bool local_storage)
{
    return protocol == 1 && schema >= 5 && storage && receipts && local_storage
           && internal_free >= 24576 && largest_block >= 8192;
}
