# Studio pocket implementation

Approved scope: all 20 items in the user's 2026-10-09 roadmap. The target is a
new private **Muse Companion** app on Replit. Implementation remains incomplete.
The approved private production deployment now passes authenticated HTTP health
checks. A cached Python 3.14 interpreter was unavailable in production; rebuilding
the environment under configured Python 3.12 and invoking the module fixed startup.
The latest physical-device check
cannot see a saved Wi-Fi network. Device pairing and live verification remain.
Nothing in this ledger narrows the requested scope or substitutes unit tests for deployment,
external service confirmation, acoustic checks, or unplugged battery measurements.

Branch: `codex/studio-pocket-companion`, initial commit `fda3e86`.
The implementation is being synchronized to the feature branch above. The new
Replit app builds successfully and passes authenticated production HTTP health.
Hosted development diagnostics confirm PostgreSQL storage. The existing OpenAI
account secret is linked. Application-only firmware installation and digest
verification succeeded; all five queued recordings remain intact.

| # | Feature | Current implementation and remaining verification/work |
|---|---|---|
| 1 | Always-on Replit companion | PostgreSQL records, captures, blobs, stable owner secret; real PostgreSQL restart tests pass. Locked cloud requirements, Docker and Replit launch scripts prepared. Muse Companion has been created at https://replit.com/@LucaAndrea-Coll/Muse-Companion; source uploaded and cloud dependencies installed. Replit provides PostgreSQL. Encrypted restore passes SQLite and real PostgreSQL tests. Invite-only Reserved VM deployment is live, the approved OpenAI secret is linked, and production PostgreSQL was initialized from development data. An unauthenticated health request redirects to Replit sign-in. Authenticated production restart and physical-device connectivity remain. |
| 2 | Connection/account status | Backend observations distinguish Wi-Fi, companion, Muse pairing, Grep and work-account authorization. Native status shows bounded account summaries and a Tools recovery QR; stale summaries are explicitly labeled. Phone recovery handoff opens Work accounts and loads statuses. Live expired-account recovery on hardware remains. |
| 3 | Streaming interruptible voice | Streaming TTS with generation cancellation and resource cleanup tests. Actual native voice, follow-up/correction latency and acoustics remain; simultaneous listening/speaking requires acoustic evidence. |
| 4 | Offline controls | Durable device Done/Snooze outbox, cached todos, local focus timer. Real firmware functions pass temporary-filesystem replay/write-failure checks. Backend changes and receipts commit atomically. Offline snooze expiration, explicit retry/forget, write failures, and replay are covered by the real firmware-function harness. Unknown clocks reject snooze with a recovery instruction. Hardware action/reboot checks remain. |
| 5 | Power profiles | Responsive, Balanced (5-minute sleep sync), Travel (30-minute sleep sync); bounded connection windows and local next-sync text. Policy tests pass. A USB bench simulation logged Balanced nap and wake exactly 300 seconds apart. A second USB bench run began connected, logged its scheduled wake at 300.2 seconds, and reported connected after normal awake mode was restored. Unplugged endurance, radio-power measurement and companion reconnection remain. |
| 6 | Project/person memory | Semantic/literal retrieval with provenance, metadata filters, update/delete race protection. Tests pass. Live semantic retrieval passed a synonym query with project isolation. Browser creation, explicit literal fallback, correction and reload persistence pass. Memory editing and removal confirmation now use an in-page dialog; cancelling removal preserves the memory. |
| 7 | Segmented notebook | Device explicit Record/Pause/Resume/Important/Finish controls; compressed 15-second segments, up to 240 segments, 16-segment offline queue (about four minutes empty). Native codec/header tests pass. Backend waits for every expected segment before summary; five notebook tests pass. Phone pause stops microphone tracks; delayed permission, duplicate resume and processor failure checks pass. An 18-second physical-device recording saved two segments, paused, survived reboot, and entered Finish awaiting synchronization. Original three notes remain, for five queued files total. Live upload, marker receipt and acoustic checks remain. |
| 8 | Real work actions | Fixed Slack, Linear and Google Calendar adapters; reviewed destinations, frozen definitions, receipts, no automatic uncertain retry. Adapter tests pass. OAuth, encrypted refresh tokens, fixed destination selection and recovery UI are implemented and tested. Authorized live account grants and destination receipts remain. |
| 9 | Rich cards/handoff | Bounded diagrams, progress bars, source text, native QR handoff and same-item phone routing added. Native notebook/footer overlap fixed and visually checked. Phone-sized draft handoff, editing and reload persistence pass with no console warnings/errors or horizontal overflow. Live QR round trip remains. |
| 10 | Safe updates | Native OTA gate checks companion protocol, storage schema/features, actual local storage probe, free/largest internal memory. Preserve partition offsets and existing credentials. Full 32 MB recovery image saved. Default C5 and alternate BOX-3 builds pass. Application-only flash digest verified, healthy boot captured, storage probe passed and all three queued notes retained. A local candidate manifest records component hashes and the private recovery images. A known-good production pair and connected OTA checks remain. |
| 11 | Morning brief | Saved calendar/project/commitment context, three priorities, freshness/coverage, schedules. Tests pass. Google Calendar and Linear ingestion is implemented with bounded pagination, cancellation/completion handling, source provenance and freshness. The real local scheduler generated an OpenAI brief, delivered its completed card over WebSocket, and preserved it without duplication after restart. Real grants and production scheduled verification remain. |
| 12 | Meeting/1:1 prep | Linked people, meetings, docs, decisions and commitments; evidence IDs checked. Tests and a real OpenAI draft using synthetic context pass. Phone-sized handoff passes. Live work-source verification remains. |
| 13 | Decision/commitment register | Typed state/rationale/owner/due, suggested vs agreed, history and revision conflict handling. Voice creates suggestions. Tests pass. The phone history view exposes saved versions, ownership, rationale, alternatives and source dates/links. Browser creation as suggested, review as agreed, changed rationale, and retrieval of both versions pass. |
| 14 | Blocker exceptions | Deduplication based on meaningful state, grouped project cards, quiet hours, dismiss/snooze and digest. Tests pass. Browser creation, dismissal, revision and reopening after a changed next step pass. Calendar/Linear sync implementation and deduplication tests pass; real grants and live ingestion remain. |
| 15 | Reviewed Replit prototype | Official MCP OAuth client, discovered-schema validation, read tool allowlist, separate create/update/publish proposals, review UI. Tests pass. Signed-in Replit account and actual private prototype/app verification remain. |
| 16 | Feedback experiments | Clusters retain exact evidence, independent source counts, proposed experiment/success measure. Tests and an actual OpenAI synthesis pass: three synthetic comments are correctly represented as two source groups. Cluster-only source links are retained. Phone-sized handoff passes. Real-source verification remains. |
| 17 | Demo prep | Project app link/story/limitations and only configured read-only readiness checks, results labeled. Tests pass. A real OpenAI draft with synthetic context and phone-sized handoff pass. Real configured checks and actual rehearsal remain. |
| 18 | Evaluation | Matched baseline/candidate cases, measured success/latency/cost/product deltas, explicit missing evidence. Deterministic tests and an end-to-end synthetic comparison pass. Only matched evidence contributes citations. Phone-sized presentation passes. |
| 19 | Project research | Primary-domain web search, cited/dated findings, URL/date dedupe, project context and durable daily schedules. Tests pass. Actual research returned a cited official announcement; its September 10, 2026 publication date was independently verified. A later real scheduler run delivered a dated research card over WebSocket and survived restart without duplication. Production delivery and real project setup remain. |
| 20 | Closeout/leadership drafts | Saved wins/decisions/blockers context, editable revisioned drafts, copied text, reviewed work-action proposals. Tests pass. A real OpenAI draft with synthetic context and phone-sized handoff pass. Actual source coverage and authorized destination confirmation remain. |

## Evidence so far

- Current companion suite: **131 passed**, one upstream deprecation warning.
- PostgreSQL integration and encrypted restore tests use a real temporary server.
- Native host suite: **239 passed, zero skipped**, after installing the host PSA
  library. The complete suite passed again after SPIFFS recovery changes.
- The browser microphone lifecycle check passes duplicate-resume, permission-after-
  finish, pause/track shutdown and processor failure cases.
- ESP-IDF **v6.0.1** builds pass for the pocket 1.75C, default C5 and alternate
  BOX-3. Pocket app: **3,543,040 bytes**, **651,264 bytes free** (15.5% of 4 MB).
- The physical Waveshare 1.75C boot confirms **32 MB flash and 8 MB PSRAM**.
  A recovery image now contains the lower 16 MB captured before writes and the
  unchanged upper 16 MB. SHA-256:
  `c365e95dd87ac0d0cdce6f71f7e4fca749dcda8564e91ed5e77e81c4ef057bc5`.
- Application-only flash at `0x20000` was independently digest-verified. The
  partition table, NVS and capture partition were preserved. Boot and real storage
  probe pass; queued recordings remain **3**, storage used **183,983 bytes**.
- Actual timer pause testing exposed SPIFFS refusing rename over an existing
  file. Atomic replacement now stages the previous file as a recoverable backup;
  read and delete paths handle interrupted replacement without reviving deleted
  data. The expanded storage probe creates and replaces a file on the device.
  Native harnesses model the SPIFFS collision and injected replacement failures.
  The physical timer now resumes and pauses successfully. These checks do not
  establish immunity to filesystem corruption during an abrupt power loss.
- Notebook/footer layout is visually verified. Bench card actions call the real
  LVGL callbacks; this does not substitute for physical touch-sensor testing.
- The pocket build now uses one LVGL drawing worker. Comparable 20-second avatar
  samples without a nearby saved network showed free internal RAM rising from
  **31,231 to 37,827 bytes** and the largest free block from **16,384 to 24,576
  bytes**. Average refresh time was **5,386 vs 5,350 microseconds**, with zero
  over-budget refreshes in both samples. All five queued files and **324,794
  bytes** of used capture storage were retained. This does not establish memory
  headroom while the companion and voice are connected.
- A USB bench simulation exercised Balanced's nap/wake schedule: nap at 0.2 s,
  wake at 300.0 s, then normal awake mode restored. The saved Wi-Fi networks were
  unavailable; the evidence verifies scheduling, not successful reconnection or
  unplugged endurance. USB keeps the CPU at full speed, and radio power was not
  measured.
- The device has reconnected to a saved Wi-Fi network and obtained an IP address.
  A second Balanced USB bench cycle logged nap at 0.2 s and wake at 300.2 s;
  Wi-Fi was connected after normal awake mode was restored. Production companion
  connectivity remains pending device credential provisioning.
- Actual OpenAI calls pass: project-scoped semantic recall (0.92 s), streamed TTS
  (first chunk 0.72 s; 105 chunks), transcription round trip and a validated Studio
  morning report. Measurements are individual observations, not latency guarantees.
- Additional real OpenAI drafts pass with synthetic context: meeting preparation
  (17.52 s), prototype brief (32.99 s), feedback (15.48 s), demo (30.34 s), and
  closeout (22.76 s). Their output identifies missing live evidence and separates
  proposed actions from commitments. The evaluation path calculates measured
  deltas without calling a model. No external work action was sent.
- Source presentation now uses readable titles while retaining validated IDs in
  structured reports. Cluster-only feedback and matched evaluation cases retain
  source links; all 15 Studio tests pass after these changes.
- An actual local application scheduler generated morning, research and closeout
  drafts using OpenAI and synthetic context. All three completed cards reached a
  simulated device over a real localhost WebSocket in **48.1 seconds**, including
  the initial 30-second scheduler interval. Restart retained the same three task
  IDs and results, with no duplicate jobs for the local day. This does not prove
  production delivery or physical ESP32 connectivity.
- The scheduled research run cited the August 25, 2026 entry in OpenAI's
  [official release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes).
  The primary page independently confirmed the date and the described change.
- Decision history is now accessible from each context card. Browser verification
  preserved suggested/agreed states, the original and revised rationale, owner,
  project and source-observation time. The phone-sized dialog has no horizontal
  overflow or browser console warnings/errors.
- Memory editing no longer depends on a browser prompt, which did not open in
  the embedded browser. The in-page editor rejects blank corrections, retains
  source metadata, and preserves saved corrections after reload. The removal
  confirmation can be cancelled without deleting the memory. Phone layout and
  browser console checks pass; no real user memory was changed or deleted.
- Actual project research returned a dated official announcement. Its page and
  publication date were independently checked; this is not exhaustive coverage.
- Replit source ZIP excluded credentials/runtime data and passed an exact-value
  scan against local secrets. Its cloud package built and imported successfully.
  PostgreSQL is present. The approved OPENAI_API_KEY account secret is now linked.
  Replit's global pip user-install preference initially prevented the build;
  explicit `--no-user` installation fixed it and the cloud build passed. Hosted
  HTTP health and authenticated diagnostics confirm protocol 1, schema 5 and
  PostgreSQL storage. The user approved the $15/month Reserved VM plus separate
  database/network usage. Invite-only publishing completed, and an unauthenticated
  production health request returns HTTP 307 to Replit sign-in. Replit model-access
  verification lists all six configured text/audio models as available. The
  approved one-year production external access token is saved privately. Both a
  token-authenticated request and the signed-in browser return server errors.
  Production runtime logs report the console-script launcher as not found; its
  shebang embeds the build workspace's absolute path. The module-based launch
  command alone did not resolve the stale interpreter symlink. Inspection showed
  configured Python 3.12.12 versus cached Python 3.14.6 under `/repl/ctls`, which is
  unavailable in production. `venv --clear` rebuilt with Python 3.12.12, and the
  republished production health endpoint returns HTTP 200 with protocol 1.
  Device pairing, owner-authenticated diagnostics and production restart durability
  verification remain unverified.
- The old Mac service directory described in the October 8 validation record is
  absent on this host. Do not claim it is currently running or already migrated.

## Completion deliverables

Keep status grounded in current evidence. Supply a copyable GitHub commit title
and description when completing this large task, as the user requested. Never
include credentials in source, logs, commits, screenshots, prompts, URLs or device
frames. Authentication changes and actual Slack/calendar messages need their
specific user authorization; building the review flow does not send those messages.
