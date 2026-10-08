# Refined Muse UI

Implemented and installed on the connected Waveshare ESP32-S3-Touch-AMOLED-1.75C on October 8, 2026. This implements the Grep-inspired direction in [the design proposal](../MUSE_UI_POLISH_PLAN.md).

## Using it

- **Companion:** a shaded cream character on black, with orange activity cues. Hold the top button to talk and release to send. Touch the character for a reaction. Tap the speaker button to mute or unmute; a held tap toggles only once, and dragging away cancels it.
- **Replies:** a cream card appears as the character makes room. The latest reply remains available after speech ends. Tap the card to open Reading; Done dismisses it. A new voice turn clears the previous answer.
- **Reading:** previous/next buttons browse text at your own pace. Opening Reading pauses automatic page following. Follow voice resumes it. Back returns to the small character and reply card. Horizontal swiping still opens Settings.
- **Quiet:** swipe left, open Display, and turn Character off. This replaces the character with a dotted sphere. It does not mute audio. The preference survives a restart.
- **Motion and brightness:** Display also contains Reduced motion and Brightness. Reduced motion removes the character movement, animated expressions, orb rotation, waveform motion and answer transition; state labels still explain activity.
- **Sleep:** the bottom button retains its sleep/wake behavior. Decorative work stops while asleep or while Settings is visible.

Settings, pairing and reply surfaces use Grep cream `#FAF6F1`, orange `#FF3C00`, charcoal `#181818`, layered edges and rounded corners. Full settings rows are touch targets. The interface uses antialiased Montserrat and the existing CJK fallback font.

## Implementation

`muse_refined_ui.c` owns the full-size view; `muse_theme.c` supplies materials and typography. The original pixel renderer remains the fallback on compact displays and when the richer view cannot allocate its buffers. `CONFIG_MUSE_REFINED_UI` defaults on for the two 1.75-inch Waveshare profiles and Watcher, and can be disabled. CJK defaults on with the refined UI; an existing generated sdkconfig can still explicitly override it.

The character is an original offline 3D ellipsoid model, rendered orthographically with diffuse and specular lighting. Seven poses are stored at 224 and 96 pixels, using a shared RGB565 palette and run-length compression. The asset pack occupies **264,366 bytes**. The runtime renderer uses a bounded 150,528-byte index/pixel workspace in PSRAM. It changes poses, gently moves the character, and animates the dotted presence without a runtime mesh engine or full-screen video.

Regenerate the checked-in asset source with Python, NumPy and Pillow:

```sh
python tools/muse/render_character.py
```

`muse_reply.c` transfers normalized reply text and its speech byte offset from the chat producer through a mutex into the UI. The UI has a separate copy and never calls LVGL from a voice/network task. The 16 KiB text bound matches the current chat backend. The retained text is the current assistant message, not a conversation archive, and is cleared on a new turn or restart.

`muse_reading.c` wraps UTF-8 by measured glyph widths, preserving words and CJK closing punctuation. Changing view follows the same byte position. Explicit page navigation holds the chosen position until Follow voice or a new turn. `muse_presentation.c` prevents replaying the answer entrance after dismissal or idle/reconnect events.

Both Character and Reduced motion are stored in the existing NVS namespace. No credentials, partition layout, signing policy or physical button mapping were changed for this UI. Existing speech and optional Pocket work in the working tree was preserved; Pocket caption events also feed the new reply view.

## Installation and checks

The installed build is `build-muse-waveshare-s3-175c-bench`, built with ESP-IDF **v6.0.1**. The board identified itself in its boot log before flashing. The flashed application is **3,280,896 bytes** in the existing **4,194,304-byte** OTA slot, leaving **913,408 bytes (21.8%)**. Flash verification succeeded. NVS was preserved, and the device reconnected to its existing Muse session after reboot.

Validation completed:

- All **216 host tests** passed; the chat tests also passed after adding turn-boundary integration.
- Production UI simulator scenarios cover both **466 × 466** and **412 × 412** displays, including reply retention, Reading, Quiet, mixed CJK/Latin text, settings, setup, pairing, offline/error and voice states.
- Deterministic screenshots and a circular safe-area check pass. Pointer tests cover tap/hold/drag mute behavior, page navigation, follow/resume, interruption, and independent Character/Motion preferences.
- AddressSanitizer and UndefinedBehaviorSanitizer simulator runs pass. Leak detection is disabled because the macOS ASan runtime does not support it.
- Compact/fallback branches of UI, Settings and state code compile with warnings treated as errors.
- Actual device captures verify Companion, Quiet, Display, a retained reply and Reading. Quiet mode was set, the board rebooted, and the saved preference was verified before restoring Companion.
- The real OpenAI speech test completed: **8.05 seconds of audio**, retained text and a working Reading view. No panic, reboot loop or audio-backlog/underrun warning appeared in the captured test logs.

Short USB-powered samples on the board, against the existing 40 ms frame schedule:

| View | Average UI update | Average LVGL refresh | Maximum refresh | Refreshes over 40 ms |
| --- | ---: | ---: | ---: | ---: |
| Companion idle | 0.736 ms | 2.294 ms | 10.046 ms | 0 / 111 |
| Thinking | 0.913 ms | 2.890 ms | 5.460 ms | 0 / 155 |
| Quiet thinking | 2.518 ms | 8.453 ms | 19.525 ms | 0 / 148 |
| Speech test | 0.964 ms | 2.783 ms | 41.277 ms | 1 / 309 |

Settings and sleep samples recorded zero decorative updates. The post-speech sample had 30,959 bytes of free internal memory and 2,499,124 bytes of free PSRAM. These are sampled values, not allocation high-water measurements. LVGL timings measure refresh work, not an external measurement of panel scanout. The isolated 41.277 ms refresh slightly exceeded the frame budget.

Device images and raw measurement fields are in `simulator/screenshots/refined/device/`. The broader visual baselines are in `simulator/screenshots/refined/`. These bench checks do not establish battery endurance, long-run thermal/power behavior, finger accuracy, acoustic quality or a human-spoken dictation turn; those still require hands-on use.

## Reproduce checks

```sh
# From esp32/, with ESP-IDF v6.0.1 activated:
idf.py -B build-muse-waveshare-s3-175c-bench build
python -m unittest discover -s tests -p 'test_*.py'

# From the repository root:
cmake --build esp32/simulator/build
python esp32/simulator/tests/test_simulator.py --binary esp32/simulator/build/muse_simulator
```

The simulator compiles the real Settings UI; services underneath it are faked. Scenario keys include `reply`, `view`, `character`, `reduced_motion`, and semantic `expect_ui_*` assertions. In a screenshot-enabled firmware build, `>ui=face`, `>ui=companion`, `>ui=quiet`, `>ui=reading`, `>ui=settings` and `>ui=display` select views. Quiet/Companion previews save the corresponding display preference. `>ui.metrics` reports and resets the measurement window; serial `p` captures the screen.
