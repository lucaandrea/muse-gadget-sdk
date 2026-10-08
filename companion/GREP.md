# Grep on Muse

Muse can ask Grep company questions, search evidence, check source and connection coverage, run the approved company reports, and inspect connected Replit apps. Grep retains employee permissions, citation verification, and source coverage. No Grep secret is sent to the ESP32 or model prompts.

The deployment external-access token alone does not identify an employee. The companion also uses a restricted, parent-bound PKCE child session. It lasts at most seven days and is invalidated when its parent Grep login is removed. This requires the Grep server's exact Muse callback and restricted-session changes.

## Setup

Set `GREP_EXTERNAL_ACCESS_TOKEN` in the companion environment. Optional `GREP_ANSWER_MODEL` defaults to `gpt-6.1-sol`. Restart the service after changing environment variables.

From the companion working directory, run `muse-companion --env /path/to/backend.env grep-connect`. Open the printed URL in a browser signed into Grep on the same computer. The callback listens only on `127.0.0.1:8766` for ten minutes; it validates state and exchanges the one-time code with PKCE. The private `data/grep-auth.json` is written atomically with permissions 0600. No restart is needed after sign-in.

Run `muse-companion --env /path/to/backend.env grep-status` to check authentication, or `grep-disconnect` to revoke the child and remove its local credential. Grep's status and a disconnect control also appear in the companion's Tools view.

On the configured Mac, the installed service uses `~/Library/Application Support/MuseCompanion/backend.env` and its own `venv/bin/muse-companion`. Its launch agent is `ai.muse.pocket-companion`. Source development uses this repository; install an update into the service venv and restart the launch agent to deploy it.

## Usage

Hold the device button and ask, for example:

- “Search Grep for the latest Agent deployment guidance.”
- “What sources can Grep currently search?”
- “Which of my Grep connections are available?”
- “What did we decide about that last week?” (continues the previous Grep chat)
- “Find my Replit app, then explain how its authentication works.”

Grep answers and ranked passages include links in the companion UI. The ESP32 receives a concise caption and generated speech. Company reports keep their actual status and access limits. Unavailable connections do not become available through this adapter. Existing indexed sources exclude content that Grep itself excludes, such as private Slack DMs.

## Operational details

- Keep the companion Mac awake and reachable from the ESP32's Wi-Fi network.
- Both Grep credentials travel as HTTPS headers to the fixed `https://grep.live` origin. Redirects are never followed; deployment/login redirects fail clearly.
- Search supports new topics and follow-ups. A stable turn UUID is saved for each tool operation so an explicit retry can reuse Grep's result. Timeouts are not blindly retried.
- The server denies all Muse-session endpoints by default except the named search, status, report and inspection routes. Search podcast creation is explicitly blocked. The client exposes no write/publish/send tool.
- Auth errors, upstream outages, rate limits and partial coverage remain visible to Muse. Session renewal requires browser sign-in; the deployment token is never used as employee identity.
- Query and returned evidence can be retained in Grep chats and the existing companion operation/history store, as with the companion's other conversations.

## Verification

Run `.venv/bin/python -m pytest -q` in this directory. The Grep server has scope unit tests plus PKCE/transport/revocation integration tests in `extension.test.ts`; use its isolated-database test harness, never a production database for tests.
