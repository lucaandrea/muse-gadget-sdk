# Pocket companion validation — October 8, 2026

This records the implementation's tested boundary. It is not a claim that every
feature in the product plan has passed field testing.

## Automated checks

- The companion test suite passed **64 tests**, including the concurrently added
  Grep adapter tests and service-upgrade credential-preservation checks. It
  covers authentication and device revocation, origin
  checks, durable capture receipts, restart recovery, operation replay and
  collisions, approval races and exact saved arguments, uncertain external
  outcomes, memory correction/deletion, reminders, meeting segment replay and
  limits, cancellation, quotas, audio resampling and document-index progression.
  New checks cover reminder corrections, task revision races, persisted
  interpreter mode, language-bound capture replay, translation without tool
  execution, lesson schemas, progress recovery, quiz feedback and attempt history.
  After the final interpreter acknowledgment change, the affected 16 control and
  lesson tests passed again.
- The ESP32 host test suite passed **219 tests**, with no skips and `IDF_PATH`
  configured. This includes the filename/header regression tests and refined UI
  host tests. Recording tests check metadata, size bounds and legacy raw PCM.
- The existing headless simulator interaction test passed **1/1** before the
  separate refined-UI work. It checks tap, hold and drag interactions.
- The Waveshare 1.75C pocket firmware built successfully with ESP-IDF 6.0.1.
  The final combined app is **3,346,432 bytes**, leaving **847,872 bytes (20%)**
  of its 4 MB OTA slot. Pocket, refined UI, CJK and mDNS lookup remain enabled.

## Actual provider calls

These were real account requests, in addition to mocked unit tests. Credentials
were read privately; they were not embedded in screenshots or this report.

| Capability | Observed result |
|---|---|
| Model access | Account model catalog included the configured reasoning, Live, transcription and translation models. |
| Assistant memory | GPT-6.1 Sol saved a charger-location memory and recalled its source. |
| Voice reminder tools | The model created a reminder, changed its time and title, and completed it through actual tool calls in an isolated database. |
| Research steering | The model found an existing task and applied a correction to the same ID at revision 2. |
| Guided lessons | GPT-6.1 Sol returned validated content with five teaching steps and three questions; native practice controls were constructed from it. |
| Speech synthesis | A fixed test phrase produced 126,400 bytes of PCM audio. |
| Transcription | GPT Transcribe returned the spoken test phrase. |
| GPT-Live | Session opened, returned ready and closed cleanly. This verifies access and protocol, not acoustic quality. |
| Realtime Translate | An English train-station question produced a Spanish transcript and 281,600 bytes of translated audio. |
| Durable translation | A second real call returned “¿Dónde está la estación de tren?” and 320,000 bytes of PCM; the persisted translated capture completed without invoking assistant tools. |
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
- Storage repair was installed by the separate **Build and install circular
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
- The current hostname endpoint is `https://Lucas-M-Max-93.local:8765`. Device authentication uses
  a separate revocable credential. The new transport does not provision the
  OpenAI key to the ESP32. An older direct-TTS key may still exist in NVS until
  explicitly migrated using the documented command.

### Final continuation check

- Flashed only the application at `0x20000`, preserving the existing partition
  table, NVS, pairing, TLS setup and recording storage. Esptool verified the hash.
  Final app SHA-256:
  `8de4580529f2a8cd98dbed80fa2b55a93e3ed8f6c6815fe963c26006bd82be18`.
- The board booted through `starting` with its correct model name and no panic,
  abort or brownout in the captured boot window. It reported recording format 2,
  assistant capture mode, configured companion credentials and zero queued notes.
  Its real write/flush/rename/read storage check passed again: 2,884,241 usable
  bytes and 502 used. The idle sample showed 22,291 free internal bytes, a 10,752
  byte largest internal block and 4,407,260 free PSRAM bytes.
- The installed HTTPS service reports healthy, SQLite schema 4, guided-practice
  support and the configured `run_companion_tests` workflow. Its existing memory
  and task counts were preserved through the upgrade.
- A temporary revocable device exercised interpreter start, both directions,
  End and reconnect against the installed HTTPS/WebSocket service. The test
  credential was revoked. This is protocol verification, not a microphone test.
- The configured fixed computer runner completed the actual companion tests
  with exit code 0 (58 tests at that point, before the six lesson tests).
- The board remains offline: its saved Wi-Fi networks are not currently visible.
  A subsequent Wi-Fi check reapplied Replit and MissionHub from the existing
  `WORK_SSID` / `WORK_WIFI_PASSWORD` and `HOME_SSID` / `HOME_WIFI_PASSWORD`
  values in `.env`. Both networks survived restart and automatic scanning/retry
  was observed. No additional `CURRENT_*` variables are needed. Replit still
  reports `not_nearby`; native interpreter playback and lesson actions over the
  physical board's live network await an available compatible network.

## Browser verification

The phone interface was exercised using an isolated local preview database:
sign-in, navigation and memory creation were checked in the browser. The layout
was checked at desktop size and at a 390 × 844 phone viewport. Preview data is
separate from the installed service. A self-signed certificate warning was not
bypassed; trusted HTTPS is required for phone microphone access.

The continued implementation also verified **Change request**, interpreter
language switching/End, and guided practice through the actual browser controls
in an isolated preview. Correct and incorrect quiz answers produced the proper
feedback. The 390 × 844 phone layout shows complete quiz content and controls.
Screenshots: `interpreter-controls.png` and `guided-practice.png`. The temporary
preview server and browser tab were closed after verification.

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
- Live meeting captions, speaker identification,
  native generated-image layouts, adaptive lesson diagrams/circular timers, sandboxed
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
