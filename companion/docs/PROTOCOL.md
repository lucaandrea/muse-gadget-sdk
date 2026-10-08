# Device protocol v1

Connect to `/v1/device` over verified `wss://`, with
`Authorization: Bearer DEVICE_CREDENTIAL`. Browser clients use the same-origin
HTTP-only owner session cookie. Origin mismatches and revoked credentials are
rejected. No key goes in a URL.

Server JSON messages contain `v: 1`; the initial `hello` declares protocol,
audio rate, device ID and recording limit. Unsupported firmware protocols
should be migrated explicitly, not guessed.

## Voice

1. Client sends `voice.begin` with `id` (8–96 characters), uint32 `generation`,
   and `mode` (`recorded`, `live`, or `translate`). Translation also includes
   target `language`, an ISO language code from the UI's allowlist.
2. Wait for `voice.ready` with matching generation.
3. Send binary messages: four-byte little-endian generation followed by signed
   PCM16 little-endian mono at 16,000 Hz. Maximum message size is 32,772 bytes.
4. `voice.end` ends the microphone input. Recorded mode persists the complete
   capture before sending `voice.received {id}`. The device may then delete its
   flash copy. ACK does not mean the AI work completed.
5. `heard` and `caption` carry text; `card` carries a typed native card.
   Binary output uses the same PCM format and generation, paced for playback.
   `voice.done` means the response stream finished. Drain buffered audio.
6. `voice.cancel` invalidates playback and closes a Live session. It does not
   roll back or cancel already dispatched app actions. Those remain in task state.

The firmware sends 15-second maximum recordings. The backend accepts up to 30
seconds. Live mode keeps microphone audio off while a half-duplex client plays
speech; it sends silence upstream to finish the turn. Explicit close or the
server deadline ends the billable session. There is no invented upstream
`output_audio.done` event.

## Cards, reminders and actions

`card` contains `id`, `kind`, `title`, `body`, `source`, `status`, and up to
three `{id,label,action}` buttons. Content is text, never HTML or executable
LVGL code. Device capacity is eight recent cards; the phone has the full inbox.

`action {id,action,operation_id,minutes?}` requests a permitted transition.
`action.result` confirms the actual saved outcome. Never display successful
execution based solely on a button press. Offline actions are explicitly refused
and remain reviewable. Retry ambiguous external writes by inspecting state,
not by inventing a new operation ID.

`sync` carries `server_epoch` and up to eight upcoming/due reminders. Cache the
reminder list only when it changes. Use the authenticated server clock plus
monotonic elapsed time during the current boot. An offline cold boot needs a
valid retained/system clock. `network {ssid}` enables configured arrival
reminders; it conveys a saved network name, not GPS coordinates.

`memory.invalidated` tells devices to discard cached memory/answer cards.
`ping` / `pong` keeps a quiet connection alive. Clients send a ping every 15
seconds; the server closes after 60 seconds without input.

A clean server close stops ESP-IDF's WebSocket task, so firmware restarts it;
transport failures use the client's bounded automatic reconnect. Every reconnect
gets current due reminders and pending approvals. Durable captures and tasks are
independent of the network connection.

Bench-only USB commands include `pocket.status` and `pocket.ask=TEXT`. The latter
sends a normal authenticated `chat` with an operation ID; it is useful for
testing the exact device network/speaker path without recording the room.
`speech.test` requests a fixed AI-generated test phrase. Credential commands
are handled before the generic logger and never echoed.
