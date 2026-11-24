# Personal Mac mini hub

This fork tracks CLIProxyAPI **v8.1.2**. The `mini-hub` branch adds
`soonest-reset-first` routing and a native macOS installer. Upstream remains the
`upstream` remote; the unmodified upstream branch is `main`.

The design follows [Theo's hub guide](https://gist.github.com/t3dotgg/59a521d2ee8d70aaf90e280c175072af):
the Mini holds separate OAuth grants for your Claude and Codex accounts; your
other devices connect privately through Tailscale. This is a proxy, so the Mini
does not need to run model inference.

## Install on the Mini

Requirements: Apple Silicon macOS, Python 3.9+, Git, Go matching `go.mod`, and
Command Line Tools with a macOS SDK. The installer sets the SDK path explicitly,
so it does not require full Xcode or acceptance of an unrelated Xcode license.
Install and sign in to the standalone [Tailscale app](https://tailscale.com/download/mac).

```sh
python3 scripts/mini-hub/install.py
hubctl serve
hubctl login codex
hubctl login claude
hubctl status
```

If `~/.local/bin` is absent from your shell's PATH, use the full path to these
commands. The installer leaves your normal Claude/Codex configuration alone.
Subsequent installations replace the binary, preserve the previous binary as
`cli-proxy-api.previous`, retain existing configuration and keys, and restart
the service. Review changes before updating; this fork does not auto-update
the proxy or the pinned management panel.

`hubctl serve` enables private HTTPS on Tailscale port 8317. The first run may
ask you to enable HTTPS in the Tailscale admin console. It uses Serve; Funnel
and public port forwarding are unnecessary. Tailscale permissions should only
allow devices and people you intend to access this personal hub.

The service starts automatically **at user login**, restarts after a crash, and
holds a `caffeinate -is` assertion while running. Keep the Mini powered and
logged in; locking the screen is fine. This user service does not run after
logout or before login after a restart. It does not change FileVault, automatic
login, power-loss recovery, or system-wide power settings.

When `/Applications/Tailscale.app` is installed, a second user login job opens
Tailscale in the background. This works independently of Tailscale's own
Start on Login preference. Existing Tailscale sign-in and background Serve
configuration are retained.

## Daily use

```sh
claude-hub
codex-hub
grok-hub
gemini-hub
agy-hub
kimi-hub
opencode-hub
muse-hub
hubctl run <command> [args...]
hubctl status
hubctl models
hubctl mcp
hubctl sync-clients
hubctl restart
```

The isolated wrappers use `~/.claude-hub`, `~/.codex-hub`, `~/.grok-hub`,
`~/.gemini-hub`, `~/.kimi-hub`, and `~/.opencode-hub` so your normal CLIs keep
their existing logins and histories. Claude preferences are copied on first
installation. Codex uses Responses + WebSockets with `CLIPROXY_API_KEY`; Grok
uses `GROK_XAI_API_BASE_URL` / `GROK_MODELS_BASE_URL` + `XAI_API_KEY`; Gemini
uses `GOOGLE_GEMINI_BASE_URL` + `GEMINI_API_KEY`; Kimi (`kimi-code`) uses
`KIMI_CODE_HOME` + `KIMI_API_KEY` (`type = "openai"`); OpenCode exposes all
active hub provider groups (`claude`, `codex`, `antigravity`/`gemini`, `xai`,
`kimi`, `meta`, etc.) via `{env:CLIPROXY_API_KEY}`; Muse Code (`muse-hub`)
injects `META_API_KEY` and per-run `-c endpoint_transport.base_url=...` /
`-c endpoint_transport.auth="bearer"` overrides without modifying
`~/.config/muse/settings.json` or `XDG_CONFIG_HOME`. `hubctl run <command>`
injects hub environment variables for all providers (including OpenAI,
Anthropic, Gemini, xAI, Kimi, and Meta/Muse SDKs) directly into a child process
without printing keys.

Antigravity CLI (`agy`) always reads `~/.gemini/antigravity-cli/settings.json`
rather than a separate config directory. Opt into hub routing on a machine with
`hubctl agy-mode hub` (sets `"modelProvider": "gemini"`), then launch `agy-hub`.
Switch back to native Google sign-in anytime with `hubctl agy-mode account`.

Default MCP servers live in `~/.config/cliproxyapi-mini/mcp-servers.json`
(seeded from `scripts/mini-hub/mcp-servers.json` and included in `export-client`
bundles; inspect with `hubctl mcp`). Run `hubctl sync-clients` after adding new
providers, editing `mcp-servers.json`, or changing the hub URL to populate
`~/.opencode-hub/opencode.json` and `~/.kimi-hub/config.toml` from `/v1/models`,
keep `~/.codex-hub/config.toml` in sync, and non-destructively merge the
default MCP servers into every client CLI (`claude-hub`, `codex-hub`,
`grok-hub`, `gemini-hub`, `kimi-hub`, `opencode-hub`, `muse-hub` via
`~/.config/muse/settings.json`, and Antigravity
`~/.gemini/config/mcp_config.json`).

Add any built-in provider on the Mini hub with `hubctl login <provider>`:

```sh
hubctl login claude
hubctl login codex          # add --browser for browser OAuth instead of device flow
hubctl login antigravity
hubctl login xai
hubctl login kimi-ai        # or: hubctl login kimi
hubctl login meta
hubctl login copilot
hubctl login devin
hubctl import-vertex /path/to/service-account.json
```

All login commands also accept `--no-browser`, `--oauth-callback-port <port>`,
and `--login-proxy <url>`. For Codex, the helper automatically enables the
credential's **upstream** `websockets` flag; enabling only the client's
`supports_websockets` setting is insufficient in this release. Use
`hubctl enable-websockets` after adding Codex credentials through another UI.

To update only `hubctl` and the client wrappers on the Mini without rebuilding
or restarting the running proxy service, run `python3 scripts/mini-hub/install.py --helpers`.

Open `/management.html` on the local or Tailscale hub URL for account and quota
management. `hubctl copy-management-key` copies the management password to the
macOS clipboard without printing it. Paste it into the management panel's key
field. `hubctl copy-api-key` copies the separate client access key. Clear the
clipboard after use if appropriate. The management key grants full account
administration; do not include it in client exports.

## Connect other Tailscale machines (macOS, Linux, iPhone)

Install Tailscale on each device and sign in to the same tailnet. Keep all
upstream OAuth grants on the Mini hub only — never copy `~/.cli-proxy-api/` to
client machines. For each macOS or Linux machine, create a private client
bundle with its own dedicated access key:

```sh
hubctl export-client ~/mini-hub-macbook --name macbook
```

Using `--name <machine>` registers a separate key in `access.api-keys` on the
hub so each machine can be audited (`hubctl clients`) or revoked individually
(`hubctl revoke-client macbook`) without affecting other devices. Omitting
`--name` exports the primary access key.

Transfer that directory privately to the client machine. It contains only the
client **access key** — no upstream OAuth grants or management key. On the
client machine, install Python 3.9+ and whichever CLIs you want to use, then run:

```sh
cd mini-hub-macbook
python3 install.py --client
hubctl sync-clients
hubctl status
claude-hub
codex-hub
grok-hub
gemini-hub
kimi-hub
opencode-hub
```

Run `hubctl status` from that machine to verify the complete remote network
path and see every model provider served by the hub. Delete the transfer copy
after installation; the installed private configuration still contains the
access key. The exported helper supports macOS and Linux clients; the native
server installer requires macOS.

On iPhone, connect Tailscale and open the hub's `/management.html` in Safari.
Claude Code and Codex still run on the Mini or laptop. An app such as T3 Code
must point its provider-hosting machine at these hub configurations. The phone
does not run the desktop CLIs. T3 Code's Codex provider must use the existing CLI
configuration; its direct ChatGPT sign-in does not select this proxy.

## Routing and defaults

- New sessions prefer the earliest **weekly** subscription reset reported by
  Claude/Codex, with a shorter window used if no weekly reset is available.
- Quota observations expire after 15 minutes. Relative reset values are anchored
  to their original observation time. Past, malformed, and implausible values
  are ignored. The scheduler uses passive response signals, not estimated
  balances or background inference calls.
- Unknown/stale accounts receive a new session to learn their quota. When
  known observations exist, sampling an unknown account is limited to once
  every 15 minutes. Equal resets and pools without usable quota information
  fall back to round-robin.
- Existing credential priority, disabled-state, cooldown, and model eligibility
  checks still apply. Priority takes precedence when placing a new session.
- Session affinity lasts one hour and includes child sessions. Quota/auth
  failures can rebind; transient failures retain the original binding.
- There are three additional credential retry rounds, a maximum cooldown wait
  of 30 seconds, persistent cooldowns, SSE heartbeats, request compression, and
  Codex WebSocket compression. Model selection stays with the client; the hub
  does not silently substitute a cheaper model.
- The proxy binds to `127.0.0.1`, authenticates API and management traffic with
  separate random keys, and trusts forwarding headers only from localhost.
  Logs rotate within 128 MB; full request logging and profiling are disabled.
- Management Center **v1.29.1** is pinned with a SHA-256 check for fresh installs.
  Existing panel files are retained on reinstallation.

Account ordering requires useful headers from the provider. Until those arrive,
the fallback and sampling behavior applies. With one account per provider there
is no second account to fail over to. Repeated prompts on one account cannot
prove that a real multi-account pool balances correctly; the regression tests
exercise that behavior with controlled quota signals.

## Files and recovery

[Persistent token totals](docs/usage-tracking.md) describes the local usage dashboard,
its separate password, SQLite storage, daily backups, and restart checks.

| Path | Purpose |
| --- | --- |
| `~/.config/cliproxyapi-mini/config.yaml` | Effective v8 server configuration; contains the client key and hashed management secret |
| `~/.config/cliproxyapi-mini/secrets.json` | Original API, management, and named client keys, plus the private remote URL |
| `~/.config/cliproxyapi-mini/client.json` | This machine's endpoint and API key |
| `~/.config/cliproxyapi-mini/mcp-servers.json` | Default MCP servers synced across all client CLIs and exported bundles |
| `~/.claude-hub/`, `~/.codex-hub/`, `~/.grok-hub/`, `~/.gemini-hub/`, `~/.kimi-hub/`, `~/.opencode-hub/` | Isolated per-CLI client directories (no plaintext keys stored) |
| `~/.cli-proxy-api/` | Upstream OAuth grants; private to the Mini |
| `~/.local/share/cliproxyapi-mini/` | Installed binary, helpers, and rotating logs |
| `~/Library/LaunchAgents/com.vahaah.cliproxyapi-mini.plist` | Per-user launchd service |
| `~/Library/LaunchAgents/com.vahaah.cliproxyapi-mini.tailscale.plist` | Starts Tailscale at user login |

Configuration files use mode `0600`; private directories use `0700`. Never commit
these files or transfer the upstream credential directory to clients. Back
them up only through your private, encrypted backup system.

To stop the hub, run `hubctl stop`; to load it again, use `hubctl start`.
To remove Tailscale serving for this port, run
`tailscale serve --https=8317 off`. A rejected/revoked upstream grant needs a
fresh `hubctl login` on the Mini. Expired quota should be allowed to recover
naturally rather than repeatedly clearing cooldowns.

To update from upstream, fetch tags and merge a reviewed stable release into
`mini-hub`, resolve conflicts, run `go test ./...` and the routing race tests,
then reinstall. Keep the reset selector and service factory tests together.
The fork intentionally uses the existing custom-selector path rather than
changing the upstream scheduler or deprecated management endpoints.

Subscription proxying carries the risk described in Theo's guide. Use your own
accounts for your own work; keep Claude subscription traffic in Claude Code.
