# Muse pocket companion

A private backend and phone web interface for the round Waveshare Muse. The
device handles touch, microphone capture, native cards and speaker playback.
The backend owns memories, reminders, tools, recordings and ongoing work.

## Start here

```sh
cd companion
uv sync --extra test --locked
uv run muse-companion doctor
uv run muse-companion serve
```

Open `http://localhost:8765`. Sign in with the private value in
`companion/data/owner-token`, **not** your OpenAI key. The service reads
`OPENAI_API_KEY` from the repository's existing `.env` as data, without shell
expansion. It does not print the key. State is in `data/muse.sqlite3`; keep the
entire `data/` directory private and backed up. This is a single-owner service,
with several revocable devices sharing that owner's memories and conversation.

Try:

- “Remember that the spare charger is in the blue suitcase.”
- “Where did I put the spare charger? Give me its source.”
- “Make a task to pack it, and remind me tomorrow at 8:30 AM.”
- “Draft a 30-minute calendar event tomorrow at 10 AM to review the prototype.”
- “Research three ways to improve battery life on an ESP32-S3 AMOLED device.”

Calendar approval creates an `.ics` file for import. It does **not** silently
connect a calendar account, invite participants or send messages. External
accounts must be connected separately.

## Implemented surfaces

| Plan feature | Implementation and present boundary |
|---|---|
| 1. Natural conversation | Device hold-to-talk → durable capture → transcription → tool-using assistant → spoken reply. New presses interrupt playback. Phone additionally supports GPT-Live with client delegation. Device full-duplex GPT-Live/AEC is not enabled. |
| 2. Personal memory | Explicit capture, source/date, keyword recall, correction, deletion, cache invalidation and bounded conversation history. No implicit ChatGPT memory access. |
| 3. Reminders | SQLite scheduler, Done/Snooze/Change time, device cache and saved-Wi-Fi arrival triggers. Offline acknowledgments require reconnection. Offline cold boot needs a valid system clock before due-time evaluation. |
| 4. Briefings | On-demand or daily local-hour briefings from saved tasks, reminders, memories and ingested notifications. External calendar/email data needs a configured integration. |
| 5. App actions | Native/phone review, immutable proposed arguments, operation receipts and single execution. Local task inbox and calendar-file workflow work immediately; authenticated HTTP adapters are configurable. |
| 6. Notification filtering | Ingestion endpoint, sender rules, digest, quiet hours, explanations and feedback. Optional Decisions adapter (`MUSE_DECISIONS=1`), disabled by default. |
| 7. Background research | Persistent jobs, web search, citations, progress, cancellation and safe retry of failed analysis. Uses our scheduler and Responses; not the beta Agents execution service. |
| 8. Meeting notebook | Explicit phone recording, bounded 20-second uploads, marked segments, transcript and decision/follow-up extraction. Also accepts uploaded recordings. Segmented captions, not word-by-word live transcription; no speaker identification. |
| 9. Interpreter | Two phone language controls, dedicated Realtime Translate, paired transcripts, synthesized audio and backend 16/24 kHz conversion. Alternating turns avoid acoustic feedback. |
| 10. Documents | Upload, vector-store indexing, File Search with citations, original download and deletion. Index completion is visible. |
| 11. Vision | Phone camera/photo upload; metadata stripped, size reduced, vision reasoning returned to the assistant. No ESP32 camera assumed. |
| 12. Coaching | Durable short lessons, questions and saved learning progress. The first UI uses readable cards; adaptive lesson diagrams/timers remain future UI work. |
| 13. Home controls | Fixed authenticated HTTP workflows with approval. Actual home devices require a reachable owner-configured bridge. |
| 14. Computer work | Fixed executable/argument workflows, output limits and timeout. Opt-in local runner, **not a sandbox**. No model-generated shell commands or arbitrary browser control. |
| 15. Task-aware screen | Bounded typed cards, native text, scrollable detail and up to three tested actions. Oversize approvals require full review on the phone. Image generation is available on the phone; device image layouts remain separate work. |

## Connect a device over TLS

For a LAN installation, use this computer's stable LAN address. A DHCP
reservation avoids reprovisioning after an address change.

```sh
uv run muse-companion local-tls --address 10.0.1.66
uv run muse-companion serve --host 0.0.0.0 --port 8765 \
  --cert data/local-cert.pem --key data/local-key.pem
uv run muse-companion pair --url wss://10.0.1.66:8765/v1/device \
  --ca data/local-cert.pem
```

The pair command writes a mode-0600 JSON file containing only the revocable
device credential, URL and CA. It never contains the OpenAI key. With pocket
firmware installed:

```sh
../esp32/tools/muse/pocket.py --port /dev/cu.usbmodem2101 \
  --pairing data/pairing-DEVICE_ID.json
```

Use the ESP-IDF Python environment if `pyserial` is unavailable. Restart the
device, then use `pocket.py --status` to verify configured/connected/storage.
The TLS certificate and hostname are verified. A LAN self-signed certificate
needs manual trust setup on a phone; it is never bypassed automatically. For
normal phone access, use a public hostname with a valid HTTPS certificate.

The LAN backend requires this Mac to remain awake and reachable. Away-from-home
use needs an always-on HTTPS backend or a reachable private network. A phone
hotspot alone does not make the Mac's LAN address reachable.

On this Mac, `uv run muse-companion install-service` installs the per-user
`ai.muse.pocket-companion` launch agent. It starts at login and restarts after a
crash. The installed package, private configuration and runtime data live in
`~/Library/Application Support/MuseCompanion/`, independently of the checkout.
Its sign-in key is `data/owner-token` under that directory and logs are in
`data/service.log`. The first install copies the development database; later
installs preserve the service database. Rerun `uv run muse-companion install-service`
from `companion/` to deploy code or `.env` changes. For a restart without an update:

```sh
launchctl kickstart -k gui/$(id -u)/ai.muse.pocket-companion
```

Stop/unload it with `launchctl bootout gui/$(id -u)
~/Library/LaunchAgents/ai.muse.pocket-companion.plist`. Stopping the service
temporarily leaves device captures queued and cached reminders available.
Do not also start a development server on port 8765 while this service runs.

To disable pocket mode, use `pocket.py --disable` and restart. Muse pairing,
saved Wi-Fi and settings remain intact. If an older build stored a direct TTS
OpenAI key in device NVS, migrate it out with `>openai.key=` after verifying the
companion. The new pocket transport never sends an OpenAI key to the device.

## Firmware build

The pocket profile is deliberately limited to the verified
ESP32-S3-Touch-AMOLED-1.75C board with 32 MB flash and PSRAM.

```sh
cd esp32
. "$HOME/esp/esp-idf-v6.0.1/export.sh"
idf.py -B build-muse-pocket -DIDF_TARGET=esp32s3 \
  -DSDKCONFIG=build-muse-pocket/sdkconfig \
  '-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;devices/sdkconfig.muse;devices/sdkconfig.muse-waveshare-s3-175c;devices/sdkconfig.muse-pocket;devices/sdkconfig.muse-bench' build
```

Keep the existing SDK token in the ignored build configuration. Never put it
in a tracked defaults file. An existing generated config overrides defaults;
check `CONFIG_MUSE_POCKET=y` and the pocket partition filename.

`partitions_muse_pocket.csv` retains every existing Muse offset and adds only
`pocket` at **0xD00000, size 3 MB**. The installed board's full flash backup
showed that region erased; other factory assets were left untouched. Back up
and inspect a different device before using this partition table. Never erase
NVS, enable Secure Boot eFuses, or enable flash encryption as part of this update.

Captures are capped at 15 seconds and three queued recordings. Recording is in
PSRAM; an internal-stack worker writes an atomic SPIFFS file. A durable backend
receipt removes the local copy. A persistent sequence preserves capture order
after reconnect/reboot. Full or damaged storage is reported; damaged storage
is not automatically formatted. Reminder flash writes occur only on changes.

Recording filenames encode the full capture ID in 22 base64url characters so
both the final file and its atomic `.tmp` name fit the existing 32-byte SPIFFS
name field. Keep that filesystem setting unchanged on installed devices;
changing it changes the on-flash format. `pocket.py --storage-test` performs an
actual write, flush, rename and read check, removes its own diagnostic file,
and reports storage capacity, usage and queued captures without uploading audio.

## Workflows and credentials

Set `MUSE_INTEGRATIONS` to a private JSON file. See
[`integrations.example.json`](integrations.example.json). Hosts, methods,
executables, argument names and working directories are owner-configured.
Models cannot choose a new host, shell script or arbitrary argument list.
HTTP integrations receive `Idempotency-Key`; the destination should honor it.
Credential variables named by `token_env` must exist in `.env` or the service
environment. The installer copies only credentials referenced by configured
integrations into the private service configuration and preserves them on upgrades.
They are separate from `OPENAI_API_KEY` and are never included in tool schemas.

Only commands explicitly configured `read_only: true` can run without a review
card. A model urgency score cannot authorize execution. Approvals are bound to
saved arguments; racing/replayed approvals cannot execute the same task twice.
If a process dies during an external write, the state becomes **uncertain**.
Verify the destination before creating another action. This intentionally avoids
promising exactly-once behavior from a third-party service that lacks it.

The local process runner executes as the backend's OS user. Put it in an
isolated container/account for stronger boundaries, and expose only trusted
fixed workflows. It does not grant remote arbitrary computer control.

## Operating behavior

- General reasoning: `gpt-6.1-sol`; explicitly deep research can use
  `gpt-6-astra`; notification Decisions uses `gpt-6-luna`.
- Recorded voice: `gpt-transcribe`, then `gpt-4o-mini-tts` at 24 kHz resampled
  to 16 kHz. Live voice: `gpt-live-1`, client delegation, PCM16/16 kHz.
- Default limits: 300 provider calls/day, 30 Live minutes/day, five minutes per
  Live session. A full session allowance is reserved up front conservatively.
  These are application quotas, not a dollar-denominated billing cap.
- Live connections close at their deadline. Background work persists separately.
- External APIs, hosting and storage have their own costs. No Ultrafast premium
  processing is enabled.
- Backend restart requeues safe analysis and transcription. Ambiguous assistant
  execution needs review. Captured audio remains downloadable on failure and
  is discarded after successful processing. Meeting audio is discarded segment
  by segment after transcription; transcripts remain until removed from storage.
- Memory deletion removes the saved record and clears conversation/reply caches.
  It cannot retract information already exported or sent to an external service.
- One backend worker per SQLite database. Do not start multiple Uvicorn workers.

For a hosted deployment, [`Dockerfile`](Dockerfile) and [`Caddyfile`](Caddyfile)
provide a non-root backend and HTTPS reverse-proxy example. Supply your own
domain, hosting and account credentials. No cloud resources are purchased or
published by this repository.

## Verification

```sh
uv run pytest
node --check src/muse_companion/static/app.js
cd ../esp32
python -m unittest discover -s tests -p 'test_*.py'
```

Tests cover durable capture acknowledgment, crash recovery, reminder delivery,
idempotency, approval races, exact action arguments, memory correction/deletion,
authorization, origin checks, usage limits, meeting segment replay and bounds,
translation formats and cancellation. See [`docs/VALIDATION.md`](docs/VALIDATION.md)
for hardware and live-API evidence and remaining acceptance work.

API contract: [`docs/openapi.json`](docs/openapi.json).
Device transport: [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

Official API references: [Live](https://developers.openai.com/api/docs/guides/live),
[Responses tools](https://developers.openai.com/api/docs/guides/function-calling),
[transcription](https://developers.openai.com/api/docs/guides/transcription),
[translation](https://developers.openai.com/api/docs/guides/realtime-translation),
[File Search](https://developers.openai.com/api/docs/guides/tools-file-search),
[Decisions](https://developers.openai.com/api/docs/guides/decisions).
