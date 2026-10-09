# Private Muse Companion on Replit

The prepared app is [Muse Companion](https://replit.com/@LucaAndrea-Coll/Muse-Companion).
The invite-only production deployment is published at
https://muse-companion-lucaandrea-coll.replit.app. Authenticated HTTP health checks
now pass. A cached Python 3.14 environment pointed at a workspace-only interpreter
despite the app selecting Python 3.12. Each build now recreates the environment,
and the launch script invokes the module directly instead of using the console
script's absolute build-workspace shebang.
The user approved the $15/month
Reserved VM (0.5 vCPU, 2 GiB), plus separate database/network usage.
The OpenAI account secret is linked and its configured model access is verified.
Device pairing and authenticated production checks remain; see
[the implementation ledger](STUDIO_ROADMAP.md).

## Runtime and durable state

The root `.replit` selects Python 3.12 and a Reserved VM. The build installs
hash-pinned dependencies and the companion package. Run one application process:
it owns the device connections, scheduler and account refresh locks.

Use the Replit production PostgreSQL database for `DATABASE_URL`. Records,
recordings, original documents, settings and encrypted account grants reside in
PostgreSQL. `/tmp/muse-companion` contains only replaceable working files in hosted
mode. The app refuses hosted startup without a database and a stable owner secret.

Required secret/configuration names:

| Name | Purpose |
|---|---|
| `OPENAI_API_KEY` | Link the owner's existing Replit account key after approval. |
| `DATABASE_URL` | Replit PostgreSQL; select production data when publishing. |
| `MUSE_OWNER_TOKEN` | Stable private owner login and encryption root. Set explicitly when restoring existing data. |
| `SESSION_SECRET` | A new app may use Replit's existing persistent app secret as the owner key when `MUSE_OWNER_TOKEN` is unset. Never regenerate either key during redeployment. |
| `MUSE_PUBLIC_URL` | The final HTTPS origin, without a trailing path. Used for owner/OAuth redirects and same-item device handoff. |
| `MUSE_TIMEZONE` | Defaults to `America/Los_Angeles`. |

Keep the app **Invite only**. A device connecting through Replit's private gate
needs an External Access Token as well as its separate revocable Muse device key.
Provision the external token in the pairing JSON's `external_access_token` field.
The approved one-year production token is already stored locally in the ignored,
owner-readable `backups/studio-pocket/replit-device-access-token` file. Use the
token-only value, without a query-string prefix or `Bearer ` prefix. The pairing
file's separate `token` field must contain a Muse device key created in production.
The firmware sends the private-app token in `Authorization` and its own device
key in `X-Muse-Authorization`. Neither belongs in a URL. OpenAI and Muse SDK keys
are never sent in device frames. `MUSE_TOKEN` is the existing firmware SDK token;
the companion server does not need a copy.

Before publishing, confirm the selected Reserved VM's displayed recurring price.
After publishing, verify authenticated HTTP and WebSocket connections, durable
records across a restart, and the device compatibility/storage checks.

## Restore and recovery

Keep the stable owner key separately from backups. Exported backups are encrypted
with that key and include logical database records and retained binary content.
They do not include deployment environment variables or database passwords.

```sh
muse-companion backup --output /private/path/muse-backup.enc
muse-companion restore --input /private/path/muse-backup.enc
```

Run restore before first owner/application startup against an empty destination.
Supply the **original** owner key in `MUSE_OWNER_TOKEN`; an automatically generated
new Replit `SESSION_SECRET` cannot decrypt an older backup. Restoration refuses to
replace existing user data. Interrupted external actions become uncertain and are
not replayed automatically. Take a database-native backup when approaching the
logical export's 256 MB limit.

## Work accounts and Studio sources

Tools → Work accounts shows setup, connection, selected destination and sync
freshness. Register confidential OAuth clients and set the callback shown there:

- Slack: `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`.
- Linear: `LINEAR_CLIENT_ID`, `LINEAR_CLIENT_SECRET`.
- Google Calendar: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.

The owner must approve each provider's consent screen. Then select a specific
Slack channel, Linear team or calendar. Only that saved destination is offered
for reviewed writes. Reconnecting invalidates old approvals. Calendar/Linear
source sync can run every 15 minutes; last success, failures, pagination limits
and covered date windows remain visible. Drafts report missing source coverage.

Replit prototype work uses the official MCP connection in Tools. Review every
create/update request; publishing is a separate operation. Grep's restricted Muse
session can be imported through Tools when authorized; it is stored encrypted.

## Sources

- [Replit machine configuration](https://docs.replit.com/features/publishing/machine-configuration)
- [Replit external access tokens](https://docs.replit.com/features/deployment-customization/external-access-tokens)
- [Replit MCP server](https://docs.replit.com/platforms/mcp-server)
- [Slack OAuth](https://docs.slack.dev/authentication/installing-with-oauth/)
- [Linear OAuth](https://linear.app/developers/oauth-2-0-authentication)
- [Google server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)
