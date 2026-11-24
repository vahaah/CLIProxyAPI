#!/usr/bin/env python3
"""Install the native Mini hub, helper updates, or an exported client without sudo."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
try:
    import tomllib
except ImportError:
    tomllib = None
import urllib.request


HERE = Path(__file__).resolve().parent
USER_DIR = Path.home()
CONFIG = USER_DIR / ".config/cliproxyapi-mini"
STATE = USER_DIR / ".local/share/cliproxyapi-mini"
COMMANDS = USER_DIR / ".local/bin"
LABEL = "com.vahaah.cliproxyapi-mini"
PANEL_URL = "https://github.com/router-for-me/Cli-Proxy-API-Management-Center/releases/download/v1.29.1/management.html"
PANEL_SHA256 = "30df9503402af03e04affb55920458c13139a07757c8428fc845299876d19c43"
CLIENT_COMMANDS = (
    "hubctl",
    "claude-hub",
    "codex-hub",
    "grok-hub",
    "gemini-hub",
    "agy-hub",
    "kimi-hub",
    "opencode-hub",
    "muse-hub",
)
DEFAULT_KIMI_MODELS = (
    "kimi-k3.1-pro",
    "kimi-k3.1-pro-highspeed",
    "kimi-k3",
    "kimi-k2.8-code",
    "kimi-k2.5",
)
TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
TOML_MCP_SECTION_RE = re.compile(r'^\[mcp_servers\.(?:"([^"]+)"|([A-Za-z0-9_-]+))\]$')


def private_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".mini-hub-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def json_write(path, value):
    private_write(path, json.dumps(value, indent=2) + "\n")


def toml_key(name):
    return name if TOML_BARE_KEY_RE.match(name) else json.dumps(name)


def load_json_object(path):
    if not path.exists():
        return {}
    raw = path.read_text().strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def load_mcp_servers():
    for candidate in (CONFIG / "mcp-servers.json", STATE / "mcp-servers.json", HERE / "mcp-servers.json"):
        if candidate.exists():
            data = load_json_object(candidate)
            servers = data.get("mcpServers", data)
            if isinstance(servers, dict):
                return servers
    return {}


def read_toml_mcp_transports(path):
    """Read only transport fields (url / command+args) for [mcp_servers.*]; never read env/secrets."""
    path = Path(path)
    if not path.exists():
        return {}
    text = path.read_text()
    transports = {}
    if tomllib is not None:
        try:
            parsed = tomllib.loads(text)
        except Exception:
            parsed = {}
        mcp_table = parsed.get("mcp_servers")
        if isinstance(mcp_table, dict):
            for name, entry in mcp_table.items():
                if not isinstance(entry, dict):
                    continue
                if isinstance(entry.get("command"), str) and entry["command"]:
                    spec = {"command": entry["command"]}
                    if isinstance(entry.get("args"), list):
                        spec["args"] = [str(item) for item in entry["args"]]
                    transports[name] = spec
                elif isinstance(entry.get("url"), str) and entry["url"]:
                    transports[name] = {"url": entry["url"]}
        return transports
    current = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            match = TOML_MCP_SECTION_RE.match(stripped)
            current = (match.group(1) or match.group(2)) if match else None
            continue
        if current is None:
            continue
        for key in ("command", "args", "url"):
            m = re.match(rf"^{key}\s*=\s*(.+)$", stripped)
            if not m:
                continue
            try:
                value = json.loads(m.group(1).strip())
            except Exception:
                continue
            entry = transports.setdefault(current, {})
            if key == "args" and isinstance(value, list):
                entry["args"] = [str(item) for item in value]
            elif key in ("command", "url") and isinstance(value, str) and value:
                entry[key] = value
    return {
        name: ({"command": e["command"], **({"args": e["args"]} if e.get("args") else {})} if e.get("command") else {"url": e["url"]})
        for name, e in transports.items()
        if e.get("command") or e.get("url")
    }


def resolve_codex_mcp_servers(servers, native_codex_path):
    """Align transport with ~/.codex/config.toml so trusting $HOME never mixes url and command."""
    native = read_toml_mcp_transports(native_codex_path)
    if not native:
        return servers
    resolved = {}
    for name, spec in servers.items():
        if not isinstance(spec, dict) or name not in native:
            resolved[name] = spec
            continue
        native_spec = native[name]
        if bool(native_spec.get("command")) != bool(spec.get("command")) or bool(native_spec.get("url")) != bool(spec.get("url")):
            resolved[name] = native_spec
        else:
            resolved[name] = spec
    return resolved


def render_toml_mcp_blocks(servers, include_enabled=False):
    blocks = []
    for name in sorted(servers):
        spec = servers[name]
        if not isinstance(spec, dict):
            continue
        lines = [f"[mcp_servers.{toml_key(name)}]"]
        if spec.get("url"):
            lines.append("url = " + json.dumps(spec["url"]))
        elif spec.get("command"):
            lines.append("command = " + json.dumps(spec["command"]))
            if spec.get("args"):
                lines.append("args = " + json.dumps(list(spec["args"])))
        else:
            continue
        if include_enabled:
            lines.append("enabled = true")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_opencode_mcp(servers):
    result = {}
    for name in sorted(servers):
        spec = servers[name]
        if not isinstance(spec, dict):
            continue
        if spec.get("url"):
            result[name] = {"type": "remote", "url": spec["url"], "enabled": True}
        elif spec.get("command"):
            result[name] = {"type": "local", "command": [spec["command"], *spec.get("args", [])], "enabled": True}
    return result


def render_gemini_mcp(servers):
    result = {}
    for name in sorted(servers):
        spec = servers[name]
        if not isinstance(spec, dict):
            continue
        if spec.get("url"):
            result[name] = {"httpUrl": spec["url"]}
        elif spec.get("command"):
            entry = {"command": spec["command"]}
            if spec.get("args"):
                entry["args"] = list(spec["args"])
            result[name] = entry
    return result


def render_kimi_mcp(servers):
    result = {}
    for name in sorted(servers):
        spec = servers[name]
        if not isinstance(spec, dict):
            continue
        if spec.get("url"):
            result[name] = {"url": spec["url"]}
        elif spec.get("command"):
            entry = {"command": spec["command"]}
            if spec.get("args"):
                entry["args"] = list(spec["args"])
            result[name] = entry
    return result


def render_claude_mcp(servers, claude_settings=None):
    enabled_plugins = set()
    if isinstance(claude_settings, dict):
        for plugin_id, enabled in (claude_settings.get("enabledPlugins") or {}).items():
            if enabled:
                enabled_plugins.add(plugin_id.split("@", 1)[0])
    result = {}
    for name in sorted(servers):
        if name in enabled_plugins:
            continue
        spec = servers[name]
        if not isinstance(spec, dict):
            continue
        if spec.get("url"):
            result[name] = {"type": "http", "url": spec["url"]}
        elif spec.get("command"):
            entry = {"type": "stdio", "command": spec["command"], "args": list(spec.get("args", []))}
            result[name] = entry
    return result


def render_muse_mcp(servers):
    result = {}
    for name in sorted(servers):
        spec = servers[name]
        if not isinstance(spec, dict):
            continue
        if spec.get("url"):
            result[name] = {"type": "streamable-http", "url": spec["url"]}
        elif spec.get("command"):
            result[name] = {"type": "stdio", "command": spec["command"], "args": list(spec.get("args", []))}
    return result


def render_kimi_config(base_url, models=DEFAULT_KIMI_MODELS):
    model_list = list(models) if models else list(DEFAULT_KIMI_MODELS)
    lines = [
        "# Isolated CLIProxyAPI client; credentials come from kimi-hub.",
        "default_model = " + json.dumps(model_list[0]),
        "",
        "[providers.cliproxy]",
        'type = "openai"',
        "base_url = " + json.dumps(base_url.rstrip("/") + "/v1"),
        'api_key_env = "KIMI_API_KEY"',
    ]
    for model_id in model_list:
        lines.extend([
            "",
            f"[models.{json.dumps(model_id)}]",
            'provider = "cliproxy"',
            "model = " + json.dumps(model_id),
            "max_context_size = 262144",
        ])
    lines.append("")
    return "\n".join(lines)


def default_opencode_config(base_url, mcp_servers=None):
    base = base_url.rstrip("/")
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "cliproxy-claude": {
                "npm": "@ai-sdk/anthropic",
                "name": "Mini hub (Claude)",
                "options": {
                    "baseURL": base + "/v1",
                    "apiKey": "{env:CLIPROXY_API_KEY}",
                },
                "models": {
                    "claude-opus-5-5": {"name": "Claude Opus 5.5"},
                    "claude-sonnet-5-5": {"name": "Claude Sonnet 5.5"},
                    "claude-haiku-5-5": {"name": "Claude Haiku 5.5"},
                },
            },
            "cliproxy-codex": {
                "npm": "@ai-sdk/openai",
                "name": "Mini hub (Codex)",
                "options": {
                    "baseURL": base + "/v1",
                    "apiKey": "{env:CLIPROXY_API_KEY}",
                },
                "models": {
                    "gpt-6-astra": {"name": "GPT-6 Astra"},
                    "gpt-6.1-sol": {"name": "GPT-6.1 Sol"},
                    "gpt-6-luna": {"name": "GPT-6 Luna"},
                },
            },
            "cliproxy-gemini": {
                "npm": "@ai-sdk/google",
                "name": "Mini hub (Antigravity / Gemini)",
                "options": {
                    "baseURL": base + "/v1beta",
                    "apiKey": "{env:CLIPROXY_API_KEY}",
                },
                "models": {
                    "gemini-4-argon-high": {"name": "Gemini 4 Argon High"},
                    "gemini-3.8-flash-high": {"name": "Gemini 3.8 Flash High"},
                    "gemini-3-flash": {"name": "Gemini 3 Flash"},
                },
            },
            "cliproxy-xai": {
                "npm": "@ai-sdk/openai",
                "name": "Mini hub (xAI / Grok)",
                "options": {
                    "baseURL": base + "/v1",
                    "apiKey": "{env:CLIPROXY_API_KEY}",
                },
                "models": {
                    "grok-4.7": {"name": "Grok 4.7"},
                    "grok-4.7-build-fast": {"name": "Grok 4.7 Build Fast"},
                },
            },
            "cliproxy-kimi": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Mini hub (Kimi)",
                "options": {
                    "baseURL": base + "/v1",
                    "apiKey": "{env:CLIPROXY_API_KEY}",
                },
                "models": {
                    "kimi-k3.1-pro": {"name": "Kimi K3.1 Pro"},
                    "kimi-k3": {"name": "Kimi K3"},
                },
            },
            "cliproxy-meta": {
                "npm": "@ai-sdk/openai",
                "name": "Mini hub (Meta / Muse)",
                "options": {
                    "baseURL": base + "/v1",
                    "apiKey": "{env:CLIPROXY_API_KEY}",
                },
                "models": {
                    "muse-wonder-full-1.0": {"name": "Muse Wonder Full 1.0"},
                    "muse-spark-1.3": {"name": "Muse Spark 1.3"},
                },
            },
        },
    }
    if mcp_servers:
        config["mcp"] = render_opencode_mcp(mcp_servers)
    return config


def install_clients(client):
    json_write(CONFIG / "client.json", client)
    base_url = client["base_url"].rstrip("/")
    mcp_servers = load_mcp_servers()
    # Isolate preferences and history; authentication is injected by the wrapper.
    claude_dir = USER_DIR / ".claude-hub"
    claude_dir.mkdir(mode=0o700, exist_ok=True)
    claude_settings_path = claude_dir / "settings.json"
    if not claude_settings_path.exists():
        claude_settings = load_json_object(USER_DIR / ".claude/settings.json")
        for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
            claude_settings.get("env", {}).pop(key, None)
        json_write(claude_settings_path, claude_settings)
    else:
        claude_settings = load_json_object(claude_settings_path)
    claude_mcp_path = claude_dir / ".claude.json"
    if mcp_servers and not claude_mcp_path.exists():
        claude_mcp = render_claude_mcp(mcp_servers, claude_settings)
        if claude_mcp:
            json_write(claude_mcp_path, {"mcpServers": claude_mcp})
    codex_dir = USER_DIR / ".codex-hub"
    codex_dir.mkdir(mode=0o700, exist_ok=True)
    codex_settings = codex_dir / "config.toml"
    if not codex_settings.exists():
        codex_text = "\n".join([
            "# Isolated CLIProxyAPI client; credentials come from codex-hub.",
            'model_provider = "cliproxy"',
            "",
            "[model_providers.cliproxy]",
            'name = "Mini hub"',
            "base_url = " + json.dumps(base_url + "/v1"),
            'env_key = "CLIPROXY_API_KEY"',
            'wire_api = "responses"',
            "requires_openai_auth = false",
            "supports_websockets = true",
            # The hub already owns credential retries. Avoid multiplying them.
            "request_max_retries = 1",
            "stream_max_retries = 1",
            "",
        ])
        codex_mcp_servers = resolve_codex_mcp_servers(mcp_servers, USER_DIR / ".codex/config.toml")
        mcp_toml = render_toml_mcp_blocks(codex_mcp_servers, include_enabled=False)
        if mcp_toml:
            codex_text += "\n" + mcp_toml + "\n"
        private_write(codex_settings, codex_text)
    grok_dir = USER_DIR / ".grok-hub"
    grok_dir.mkdir(mode=0o700, exist_ok=True)
    grok_settings = grok_dir / "config.toml"
    if mcp_servers and not grok_settings.exists():
        grok_mcp = render_toml_mcp_blocks(mcp_servers, include_enabled=True)
        if grok_mcp:
            private_write(grok_settings, "# Isolated CLIProxyAPI client; credentials come from grok-hub.\n\n" + grok_mcp + "\n")
    gemini_dir = USER_DIR / ".gemini-hub/.gemini"
    gemini_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    (USER_DIR / ".gemini-hub").chmod(0o700)
    gemini_settings = gemini_dir / "settings.json"
    if not gemini_settings.exists():
        gemini_data = {"security": {"auth": {"selectedType": "gemini-api-key"}}}
        if mcp_servers:
            gemini_data["mcpServers"] = render_gemini_mcp(mcp_servers)
        json_write(gemini_settings, gemini_data)
    kimi_dir = USER_DIR / ".kimi-hub"
    kimi_dir.mkdir(mode=0o700, exist_ok=True)
    kimi_settings = kimi_dir / "config.toml"
    if not kimi_settings.exists():
        private_write(kimi_settings, render_kimi_config(base_url))
    kimi_mcp = kimi_dir / "mcp.json"
    if mcp_servers and not kimi_mcp.exists():
        json_write(kimi_mcp, {"mcpServers": render_kimi_mcp(mcp_servers)})
    opencode_dir = USER_DIR / ".opencode-hub"
    opencode_dir.mkdir(mode=0o700, exist_ok=True)
    opencode_settings = opencode_dir / "opencode.json"
    if not opencode_settings.exists():
        json_write(opencode_settings, default_opencode_config(base_url, mcp_servers))
    muse_settings = USER_DIR / ".config/muse/settings.json"
    if mcp_servers and not muse_settings.exists():
        json_write(muse_settings, {"schema_version": 1, "mcpServers": render_muse_mcp(mcp_servers)})
    for name in CLIENT_COMMANDS:
        destination = COMMANDS / name
        target = STATE / "hubctl.py"
        if destination.is_symlink() and destination.resolve() == target.resolve():
            continue
        if destination.exists() or destination.is_symlink():
            raise SystemExit(f"Refusing to replace existing command: {destination}")
        destination.symlink_to(target)


def install_tailscale_login(launch_dir, logs, start=True):
    tailscale_app = Path("/Applications/Tailscale.app")
    if not tailscale_app.exists():
        return False
    # Use Launch Services independently of the app's Start on Login helper.
    # Opening an already-running app leaves its active VPN tunnel running.
    label = LABEL + ".tailscale"
    plist_path = launch_dir / (label + ".plist")
    private_write(plist_path, plistlib.dumps({
        "Label": label,
        "ProgramArguments": ["/usr/bin/open", "-g", "-a", str(tailscale_app)],
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "tailscale-startup.log"),
        "StandardErrorPath": str(logs / "tailscale-startup.log"),
    }).decode())
    if start:
        domain = "gui/" + str(os.getuid())
        registered = subprocess.run(["launchctl", "print", domain + "/" + label], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not registered:
            subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", action="store_true", help="install an exported client bundle")
    parser.add_argument("--helpers", action="store_true", help="update hubctl and client wrappers without rebuilding or restarting the proxy")
    parser.add_argument("--no-start", action="store_true", help="prepare service without loading launchd")
    args = parser.parse_args()
    if args.client and args.helpers:
        parser.error("--client and --helpers are mutually exclusive")
    if sys.platform != "darwin" and not args.client and not args.helpers:
        parser.error("the server installer requires macOS")
    os.umask(0o077)
    for directory in (CONFIG, STATE, COMMANDS):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in (CONFIG, STATE):
        directory.chmod(0o700)
    runtime = (HERE / "hubctl.py").read_text()
    runtime = "#!" + sys.executable + "\n" + runtime.split("\n", 1)[1]
    private_write(STATE / "hubctl.py", runtime, 0o700)
    private_write(STATE / "install.py", (HERE / "install.py").read_text(), 0o700)
    bundled_mcp = HERE / "mcp-servers.json"
    if bundled_mcp.exists():
        mcp_text = bundled_mcp.read_text()
        private_write(STATE / "mcp-servers.json", mcp_text, 0o600)
        if not (CONFIG / "mcp-servers.json").exists():
            private_write(CONFIG / "mcp-servers.json", mcp_text, 0o600)
    if args.client:
        install_clients(json.loads((HERE / "client.json").read_text()))
        print(f"Installed {', '.join(CLIENT_COMMANDS)} in {COMMANDS}")
        print("Next: run `hubctl status` and `hubctl sync-clients` to sync all live hub models and default MCP servers.")
        return
    if args.helpers:
        client_path = CONFIG / "client.json"
        if not client_path.exists():
            raise SystemExit(f"Missing existing client configuration: {client_path}")
        install_clients(json.loads(client_path.read_text()))
        print(f"Updated {', '.join(CLIENT_COMMANDS)} in {COMMANDS}")
        return
    binary_dir = STATE / "bin"
    binary_dir.mkdir(mode=0o700, exist_ok=True)
    binary = binary_dir / "cli-proxy-api"
    build_env = dict(os.environ)
    # Use the installed CLT SDK, even if full Xcode awaits license acceptance.
    clt = Path("/Library/Developer/CommandLineTools")
    if (clt / "usr/bin/clang").exists():
        build_env.update(DEVELOPER_DIR=str(clt), CC=str(clt / "usr/bin/clang"))
        build_env["SDKROOT"] = subprocess.check_output(
            ["/usr/bin/xcrun", "--show-sdk-path"], env=build_env, text=True
        ).strip()
    build_env["CGO_ENABLED"] = "1"
    repository = HERE.parent.parent
    revision = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=repository, text=True).strip()
    version = subprocess.check_output(["git", "describe", "--tags", "--abbrev=0"], cwd=repository, text=True).strip()
    build_date = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print("Building native proxy from your fork...", flush=True)
    subprocess.run([
        "go", "build", "-p", "4", "-trimpath", "-ldflags",
        f"-s -w -X main.Version={version}-mini-hub -X main.Commit={revision} -X main.BuildDate={build_date}",
        "-o", str(binary) + ".new", "./cmd/server",
    ], cwd=repository, env=build_env, check=True)
    if binary.exists():
        shutil.copy2(binary, str(binary) + ".previous")
    os.replace(str(binary) + ".new", binary)
    secret_path = CONFIG / "secrets.json"
    if secret_path.exists():
        credentials = json.loads(secret_path.read_text())
    else:
        credentials = {"api_key": secrets.token_urlsafe(48), "management_key": secrets.token_urlsafe(48)}
        json_write(secret_path, credentials)
    auth_dir = USER_DIR / ".cli-proxy-api"
    auth_dir.mkdir(mode=0o700, exist_ok=True)
    auth_dir.chmod(0o700)
    config_path = CONFIG / "config.yaml"
    if not config_path.exists():
        template = (HERE / "config.template.yaml").read_text()
        for marker, value in {
            "__MANAGEMENT_KEY__": credentials["management_key"],
            "__API_KEY__": credentials["api_key"],
            "__AUTH_DIR__": str(auth_dir),
        }.items():
            template = template.replace(marker, json.dumps(value))
        private_write(config_path, template)
    config_path.chmod(0o600)
    panel = CONFIG / "static/management.html"
    if not panel.exists():
        with urllib.request.urlopen(PANEL_URL, timeout=30) as response:
            asset = response.read()
        if hashlib.sha256(asset).hexdigest() != PANEL_SHA256:
            raise SystemExit("Management panel checksum mismatch; service not started.")
        private_write(panel, asset.decode())
    install_clients({"base_url": "http://127.0.0.1:8317", "api_key": credentials["api_key"]})
    logs = STATE / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    launch_dir = USER_DIR / "Library/LaunchAgents"
    launch_dir.mkdir(parents=True, exist_ok=True)
    service = {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/caffeinate", "-is", str(binary), "--config", str(config_path)],
        "WorkingDirectory": str(STATE),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "Umask": 0o077,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "StandardOutPath": str(logs / "launchd.log"),
        "StandardErrorPath": str(logs / "launchd.log"),
    }
    plist_path = launch_dir / (LABEL + ".plist")
    private_write(plist_path, plistlib.dumps(service).decode())
    install_tailscale_login(launch_dir, logs, start=not args.no_start)
    if not args.no_start:
        domain = "gui/" + str(os.getuid())
        registered = subprocess.run(["launchctl", "print", domain + "/" + LABEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if registered:
            subprocess.run(["launchctl", "kickstart", "-k", domain + "/" + LABEL], check=True)
        else:
            subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    print(f"Installed native Mini hub. Config: {config_path}")
    print(f"Commands: {', '.join(CLIENT_COMMANDS)}")


if __name__ == "__main__":
    main()
