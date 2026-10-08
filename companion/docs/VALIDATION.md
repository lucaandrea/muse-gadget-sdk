# Pocket companion validation — October 8, 2026

This records the implementation's tested boundary. It is not a claim that every
feature in the product plan has passed field testing.

## Automated checks

- The companion test suite passed **48 tests**, including the concurrently added
  Grep adapter tests and service-upgrade credential-preservation checks. It
  covers authentication and device revocation, origin
  checks, durable capture receipts, restart recovery, operation replay and
  collisions, approval races and exact saved arguments, uncertain external
  outcomes, memory correction/deletion, reminders, meeting segment replay and
  limits, cancellation, quotas, audio resampling and document-index progression.
- The ESP32 host test suite passed **208 tests** with `IDF_PATH` configured.
  This run includes the storage filename regression tests and existing Muse
  tests. Concurrent UI changes made after this run need their own validation.
- The existing headless simulator interaction test passed **1/1** before the
  separate refined-UI work. It checks tap, hold and drag interactions.
- The Waveshare 1.75C pocket firmware built successfully with ESP-IDF 6.0.1.
  At that build the app was 2,232,320 bytes, leaving 47% of its 4 MB OTA slot.
  Later UI builds may have a different size.

## Actual provider calls

These were real account requests, in addition to mocked unit tests. Credentials
were read privately; they were not embedded in screenshots or this report.

| Capability | Observed result |
|---|---|
| Model access | Account model catalog included the configured reasoning, Live, transcription and translation models. |
| Assistant memory | GPT-6.1 Sol saved a charger-location memory and recalled its source. |
| Speech synthesis | A fixed test phrase produced 126,400 bytes of PCM audio. |
| Transcription | GPT Transcribe returned the spoken test phrase. |
| GPT-Live | Session opened, returned ready and closed cleanly. This verifies access and protocol, not acoustic quality. |
| Realtime Translate | An English train-station question produced a Spanish transcript and 281,600 bytes of translated audio. |
| Decisions | The notification classification request returned the defined urgent choice. |
| Web research | A short research request returned two source citations. |
| File Search | A temporary warranty document finished indexing; the answer retrieved its 21-day warranty with a citation. Temporary remote test resources were deleted. |
| Image generation | Flare generated a PNG, decoded and resized for the companion; see `generated-example.png`. |

## Installed device and service

- The physical board is the Waveshare ESP32-S3-Touch-AMOLED-1.75C, with
  466 × 466 display, 32 MB flash and 8 MB PSRAM.
- A full 32 MB flash backup was saved before partition changes:
  `backups/pocket-20261008/before-pocket.bin`, SHA-256
  `37963769eaf744746e8db4e8194169952dd5a7ee2756a736f24c28a21cf294bb`.
- The backup showed the new 3 MB pocket region at `0xD00000` erased.
  Existing partition offsets, pairing, saved Wi-Fi and factory assets were
  preserved. Secure Boot eFuses and flash encryption were not enabled.
- Firmware was flashed and its hash verified. It connected by verified WSS to
  the companion. A command sent through the device's actual WebSocket connection
  recalled the memory and created the requested local task. This test entered
  text over USB; it does not certify microphone recognition in a room.
- A clean backend shutdown/restart was followed by device reconnection.
- Against the installed HTTPS service, a scheduled self-test reminder became
  due, emitted its WebSocket card, accepted Done and disappeared from active
  reminders. The diagnostic reminder was left completed in its audit history.
- Storage repair is being handled in the separate **Build and install circular
  Muse app** chat. That chat reported the repaired firmware installed and its
  actual write/flush/rename/read diagnostic passing, with 502 bytes used out of
  2,884,241 usable bytes and zero queued captures. The filename fix is retained
  in this implementation.
- That same device-repair chat subsequently reported **69,120 audio frames**
  delivered (4.32 seconds at 16 kHz), with **zero speaker-driver errors**.
  This verifies the driver path; intelligibility still needs a listening check.
- A device status sample after the PSRAM allocation adjustment showed 20,027
  bytes free internal memory, largest internal block 7,168 bytes and about
  5.2 MB free PSRAM. This is one idle sample, not a simultaneous-load guarantee.
- The backend is installed as the per-user `ai.muse.pocket-companion` launch
  agent. Its package, database, owner key, device tokens and TLS key live under
  `~/Library/Application Support/MuseCompanion/`. It runs independently of this
  Codex turn, starts at user login and restarts after a crash.
- The current LAN endpoint is `https://10.0.1.66:8765`. Device authentication uses
  a separate revocable credential. The new transport does not provision the
  OpenAI key to the ESP32. An older direct-TTS key may still exist in NVS until
  explicitly migrated using the documented command.

## Browser verification

The phone interface was exercised using an isolated local preview database:
sign-in, navigation and memory creation were checked in the browser. The layout
was checked at desktop size and at a 390 × 844 phone viewport. Preview data is
separate from the installed service. A self-signed certificate warning was not
bypassed; trusted HTTPS is required for phone microphone access.

## Remaining acceptance work

- Human listening checks for intelligibility and speaker level; realistic
  microphone capture in noise; echo cancellation and no self-triggering.
  Firmware currently uses hold-to-talk. GPT-Live is exposed in the phone UI;
  simultaneous native listening and speaking remains disabled.
- End-to-end latency measurements on LAN and hotspot; a complete physical
  record/disconnect/reboot/reconnect/receipt test; offline clock-loss behavior.
- Sustained simultaneous audio/network/display load, heap fragmentation,
  battery life, heat and responsiveness after long use.
- Real external-account writes and bridge commands against owner-configured
  services. The built-in task inbox and approved ICS-file export work without
  those accounts. A separate chat is integrating Grep; its authentication and
  deployment should be checked in that chat's results.
- Away-from-home connectivity needs an always-on reachable backend or private
  network. The current Mac must remain awake on its LAN. A phone hotspot alone
  cannot reach this private address.
- Live meeting captions, speaker identification, native interpreter controls,
  native generated-image layouts, adaptive lesson diagrams/timers, sandboxed
  computer execution and beta Agents-service integration are not delivered by
  the current firmware. The README maps all 15 proposals to their actual
  implementation and current limits.

## Repeatable commands

From `companion/`:

```sh
uv run pytest -q
node --check src/muse_companion/static/app.js
node --check src/muse_companion/static/capture.js
```

From `esp32/`, with the ESP-IDF environment loaded:

```sh
python -m unittest discover -s tests -p 'test_*.py'
python tools/muse/pocket.py --port /dev/cu.usbmodem2101 --status
python tools/muse/pocket.py --port /dev/cu.usbmodem2101 --storage-test
```

Coordinate access to the USB port before running device diagnostics or flashing.
Do not run multiple companion workers against the same SQLite database.
