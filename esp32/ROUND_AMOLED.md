# Round AMOLED installation

Installed on 2026-10-08 on the connected **Waveshare
ESP32-S3-Touch-AMOLED-1.75C**, USB serial/MAC `7c:0c:5f:42:4a:4c`.
The factory firmware identified the board; esptool confirmed 32 MB flash
and the boot log confirmed 8 MB octal PSRAM and a 466 × 466 display.

## Current combined installation

The current build combines the [refined circular UI](REFINED_UI.md), the
[Pocket Companion](../companion/README.md), and the capture-storage repair.
Both `CONFIG_MUSE_POCKET` and `CONFIG_MUSE_REFINED_UI` must remain enabled;
flashing the ordinary UI profile alone disables the companion. The pocket
profile now explicitly enables both features and local hostname resolution.

The companion endpoint is `wss://Lucas-M-Max-93.local:8765/v1/device`. This
hostname follows the Mac across network changes. The installed service has a
server certificate signed by the device's existing trusted root, so certificate
and hostname verification stay enabled. The original TLS files are backed up
under the installed service's `data/tls-backup-20261008/`. Its trusted root is
`data/local-ca.pem`; the server certificate and key remain `data/local-cert.pem`
and `data/local-key.pem`. Private provisioning files now use the hostname.
The Mac must remain awake and reachable for online replies.

The “storage full/unavailable” error came from capture filenames exceeding
SPIFFS's 31-character limit, including the leading slash and temporary suffix.
The full 128-bit capture ID now uses a lossless base64url filename that fits the
existing format. The repair preserves the capture partition, saved networks,
pairing and companion credential; it does not reformat storage.

The combined build also places the inbox beside the touch-only speaker control,
clear of the screen title, and keeps card actions inside the circular safe area.
Reading hides the inbox control. Offline captures have a saved-count notice;
storage and provider failures remain visible in the refined UI.

The subsequent Pocket update adds versioned recording metadata and native
interpreter/lesson action acknowledgments. Recordings retain their translation
language across offline queuing; assistant capture remains the default.
`pocket.status` reports `recording_format: 2`. The backend also supplies guided
lesson cards with touch quizzes and saved progress. Live device use of these
new controls still needs the Wi-Fi connection described below.

The Markdown/menu/motion update is 3,543,040 bytes, leaving 651,264 bytes
(15.5%) of its 4 MB app slot free. The latest
application is `../build-muse-pocket/muse-gadget.bin`, with SHA-256
`fd9b613b06fc234ddb34bdb754ddeada3ef6c10e27156fd5942da3e9b2d8642f`.
Its application-only flash at `0x20000` was verified. The previous 4 MB
application slot was backed up to the ignored build directory as
`before-markdown-ui.bin`. The subsequent boot reached Muse Gadget starting
and the 466 × 466 UI without a panic. The preceding combined binary, private configuration
and validation records remain in the ignored `../build-muse-combined-verified/`
directory. The isolated build
source snapshot is `/tmp/muse-combined-install-20261008/`.

Storage's atomic write/flush/rename/read check passes: 2,884,241 usable bytes,
502 used bytes and zero queued captures. The built-in MP3 diagnostic decodes
58,752 samples (3.67 seconds). Device screenshots verify card controls inside
the circular safe area; the UI simulator covers 412 and 466 pixels. The current combined
firmware build passed for the connected Waveshare board. The latest firmware host run passed all 228 tests without skips,
with the ESP-IDF environment loaded. These checks do not establish physical
audibility or a live spoken turn on this network.

At the UI update check, the saved LucaGPT hotspot was not visible to the
device. Its previously verified connection and companion setup remain saved;
an online agent turn could not be repeated during this install. The existing
speaker mute preference was preserved. No credentials were printed or committed.
The earlier connected-device test played 69,120 speech frames (4.32 seconds)
with zero speaker-write errors, before this network change.

From the repository root, verify storage without uploading audio:

```sh
python esp32/tools/muse/pocket.py --port /dev/cu.usbmodem2101 --storage-test
```

The subsequent phone-pairing fix pauses the Pocket network task during a BLE
phone connection to release its 8 KiB internal stack, then resumes it when the
phone disconnects and Wi-Fi is available. Storage and queued captures stay
active. Five new supervisor tests pass in addition to the 228-test suite.
The application-only flash verified successfully. LucaGPT initially rejoined
at 172.20.10.2, then disappeared from scans after a disconnect (reason 200);
automatic retries remain active. Three queued captures remain intact
(183,481 bytes used).
Phone account setup remains unverified: the prior attempt completed Bluetooth
confirmation and network scanning but never delivered provision_v2 before
timing out. The Mac is currently on office Wi-Fi, so the local companion is
also unreachable from the device's hotspot network. See REFINED_UI.md for
the current follow-up; the earlier checks above are historical snapshots.

Use the pocket build command in the companion README for future updates and
check the generated config includes the two UI/companion flags above and
`CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions_muse_pocket.csv"`.
The remaining sections describe the original direct Muse/TTS installation,
which remains available when pocket mode is disabled.

## Account setup

The original account installation completed and reported **Online** after
Wi-Fi setup. The current re-pairing attempt is still incomplete as noted above.
For future setup after a factory reset:

1. Open **Settings → Devices** in the Muse phone app and enable Developer mode.
2. Select **MuseGadget-424A4C** and follow the Wi-Fi/account pairing prompts.
3. Press the device's **top button** when its screen requests confirmation.

The SDK token from the local `.env` variable `MUSE_TOKEN` is already compiled
into the installed firmware. Account pairing supplies a separate device token;
an unpaired device reporting “Not set up” is expected even with the SDK token.

## Automatic Wi-Fi

Both `HOME_SSID` / `HOME_WIFI_PASSWORD` and `WORK_SSID` /
`WORK_WIFI_PASSWORD` from the local `.env` were imported into the device's
persistent saved-network list. The saved SSIDs are **MissionHub** and
**Replit**. The installed SDK already supports up to eight saved networks,
so this configuration did not require a firmware rebuild.

The device reconnects automatically after a restart and searches its saved
networks when the connection is lost. When a scan finds multiple saved
networks, it tries the strongest first; it can also reconnect quickly to its
last successful network. Passwords were transferred literally over USB, with
no shell expansion, and were not printed or added to tracked files.

Both saved passwords were verified against `.env` by reading back NVS and
checking its checksums without displaying the values. A hardware reboot then
automatically reconnected to **MissionHub** and brought up the Muse control
and tunnel sessions. **Replit** is saved; its live connection has not been
verified at this location.

These settings survive normal firmware updates. A factory reset or restoring
the original factory backup removes them. Editing `.env` alone does not update
the saved entries on an already-running device; changed credentials need to
be imported again.

## Device controls

Swipe left from the character to open Settings. Swipe vertically through the
settings list, tap a row to open it, and use the back arrow to return. Once
paired, hold the top button to speak and release it to send. The bottom button
sleeps or wakes the display. Sound includes volume, microphone gain, a live
microphone meter, and display brightness.

The upper-left **speaker icon is touch only**. Tap it once to mute or unmute;
the icon and a brief caption confirm the change. A long hold also toggles once
on release. Dragging off the icon cancels the tap. Muting works during a reply,
and the preference survives a restart. The two right-hand physical buttons
keep their talk and sleep functions.

## Spoken replies

Muse still handles the conversation. Completed reply messages are now spoken
using OpenAI `gpt-4o-mini-tts` with the `coral` voice. This is an AI-generated
voice, disclosed in Sound settings. Speech streams directly over verified
HTTPS from the device, with 24 kHz PCM resampled to the codec's 16 kHz rate.
The Mac is only needed for installation and credential provisioning.
Cross-signed certificate support is enabled in ESP-IDF so OpenAI's Google
Trust Services chain validates against the built-in root bundle. Certificate
and hostname verification remain enabled.

Import `OPENAI_API_KEY` from the repository's local `.env`, from `esp32/`:

```sh
python tools/muse/tts.py --env ../.env --port /dev/cu.usbmodem2101 --test
python tools/muse/tts.py --port /dev/cu.usbmodem2101 --status
```

The helper parses the file as data, transfers only that key over USB, and never
prints it. It is stored in the device's NVS (`muse_tts` namespace), separate
from the firmware and preserved by normal updates. Updating `.env` requires
running the helper again. `>openai.key=` removes the stored key.

**Sound → Test voice** plays the same test without a USB command. The test
uses the normal reply playback path without sending a chat
message. Speaker volume must be above zero and the speaker unmuted. With the
speaker muted, replies remain readable and no new speech request is made.
HTTP failures retain readable captions and show a voice-unavailable message
afterwards. A new talk press interrupts playback; cancelled packets are
discarded by generation. Queues are bounded and slow downloads under pressure.

Replies retain up to 16,383 UTF-8 bytes per message (eight messages per turn).
Long text is split into requests below the speech API's 4,096-character limit,
without splitting UTF-8 characters. The SDK's three-minute voice-turn limit
still applies. USB typed-chat replies remain console-only, as in the SDK.

Reference: [OpenAI speech guide](https://developers.openai.com/api/docs/guides/text-to-speech).

## Interface changes

- Black AMOLED background with mint accents, clearer status typography,
  rounded controls and a progress arc with rounded ends.
- Settings viewports calculated from the circle's radius, keeping visible
  row corners inside the bezel at both 412 px and 466 px.
- A smaller battery indicator and explicit first-time pairing guidance.
- Existing animated character, voice, reply pagination, Wi-Fi, BLE, power
  management and OTA features retained.

## Validation and recovery

The installed build uses ESP-IDF **v6.0.1** and occupies **2,166,784 bytes**,
leaving **48%** of its 4 MB application slot free. Flash hashes were verified.
Secure boot, flash encryption and pairing eFuse authentication remain disabled.

Validation completed:

- Host suite: 205 tests, successful with one skipped, including PCM chunk
  boundaries, resampling, ordered speech, mute, cancellation, backpressure,
  readable failure fallback, and private credential parsing.
- Eight deterministic simulator scenarios at both 412 × 412 and 466 × 466.
- Pointer-event tests for single taps, long holds and dragging off the speaker.
- Firmware build for this 1.75C and an isolated, non-bench 1.75 build.
- Real device boot, display and CST9217 touch initialization, Wi-Fi scanning,
  BLE advertising, and live microphone capture.
- Audio self-test: both microphone channels produced samples; capture and
  playback ran close to 16 kHz. The built-in 3.67-second MP3 speaker test
  completed successfully at reduced volume; the original 70% was restored.
- Device screenshots of the home, Settings and Sound pages.
- OpenAI speech on the real device: first PCM in 2.46 seconds, 202,800
  incoming 24 kHz frames, 8.45 seconds played at 16 kHz, peak level 0.829,
  and no speaker-write errors. This verifies the digital playback path;
  audible quality still needs the person holding the device to confirm it.
- Speech key retained through the final flash/reboot; automatic MissionHub
  reconnection and both Muse control/tunnel sessions restored.

Firmware and screenshots are in `build-muse-waveshare-s3-175c-bench/`;
screenshots and a redacted boot log are in its `validation/` directory.
This build enables on-demand USB screenshots. For example, from `esp32/`:

```sh
python tools/muse/snap.py /dev/cu.usbmodem2101 '>ui=settings' settings.png
python tools/muse/snap.py /dev/cu.usbmodem2101 '>ui=face' home.png
```

The complete original flash and its SHA-256 checksum are stored in the
repository's ignored `backups/` directory as
`waveshare-175c-original-2026-10-08.bin` and the matching `.sha256` file.
Restoring that backup also restores the original device data.

The local `.env`, generated configuration, firmware binaries and backup are
excluded from Git. The firmware build helper does not import `MUSE_TOKEN`
from `.env`; that token was copied into this build's `sdkconfig` without
printing its value. To change
it later, update that build's `CONFIG_GADGET_SDK_TOKEN` and rebuild.

On this Mac, use `DEVELOPER_DIR=/Library/Developer/CommandLineTools` and activate
`~/esp/esp-idf-v6.0.1/export.sh` before building. The installed board profile is
`MUSE_BENCH=1 tools/muse/board.sh build s3`.
