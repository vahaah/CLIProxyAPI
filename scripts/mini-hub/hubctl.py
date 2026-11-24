#!/usr/bin/env python3
"""Private hub management and isolated multi-provider CLI launchers. Never print keys."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


USER_DIR = Path.home()
CONFIG = USER_DIR / ".config/cliproxyapi-mini"
STATE = USER_DIR / ".local/share/cliproxyapi-mini"
LABEL = "com.vahaah.cliproxyapi-mini"
LOCAL_URL = "http://127.0.0.1:8317"

LOGIN_PROVIDERS = {
    "claude": "--claude-login",
    "codex": "--codex-device-login",
    "antigravity": "--antigravity-login",
    "kimi": "--kimi-login",
    "kimi-ai": "--kimi-ai-login",
    "xai": "--xai-login",
    "meta": "--meta-login",
    "copilot": "--copilot-login",
    "devin": "--devin-login",
}
WRAPPER_NAMES = (
    "claude-hub",
    "codex-hub",
    "grok-hub",
    "gemini-hub",
    "agy-hub",
    "kimi-hub",
    "opencode-hub",
    "muse-hub",
)
MUSE_SESSION_SUBCOMMANDS = ("exec", "serve", "acp", "resume")
MUSE_NON_SESSION_SUBCOMMANDS = (
    "config",
    "export",
    "trace",
    "model-profile",
    "skills",
    "voice",
    "plugins",
    "sandbox",
    "schema",
    "mxp",
    "attach",
    "session-message",
    "sessions",
    "mcp",
    "auth",
    "login",
    "logout",
    "usage",
    "init",
    "help",
)
CLIENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
NON_CHAT_MODEL_RE = re.compile(r"(^|[-_])(image|video|tts)([-_.]|$)", re.IGNORECASE)
TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
TOML_MCP_SECTION_RE = re.compile(r'^\[mcp_servers\.(?:"([^"]+)"|([A-Za-z0-9_-]+))\]$')


def load(name):
    return json.loads((CONFIG / name).read_text())


def write_private_text(path, content, mode=0o600):
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


def write_private(path, value):
    write_private_text(path, json.dumps(value, indent=2) + "\n")


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
    for candidate in (CONFIG / "mcp-servers.json", STATE / "mcp-servers.json"):
        if candidate.exists():
            data = load_json_object(candidate)
            servers = data.get("mcpServers", data)
            if isinstance(servers, dict):
                return servers
    return {}


def tailscale_binary():
    for candidate in (shutil.which("tailscale"), "/usr/local/bin/tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("Install Tailscale and sign in on the Mini first.")


def request_with_headers(path, key, base=LOCAL_URL, payload=None, method=None, extra_headers=None):
    headers = {"Authorization": "Bearer " + key}
    if extra_headers:
        headers.update(extra_headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers, data=data, method=method)
    # Ignore any ambient outbound proxy for this private hub endpoint.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as response:
        return json.load(response), response.headers


def request(path, key, base=LOCAL_URL, payload=None, method=None):
    body, _ = request_with_headers(path, key, base=base, payload=payload, method=method)
    return body


def enable_websockets(credentials):
    accounts = request("/v8/management/credentials", credentials["management_key"])
    count = 0
    for account in accounts.get("files", []):
        if (account.get("provider") or account.get("type")) != "codex":
            continue
        request("/v8/management/credentials/fields", credentials["management_key"],
                payload={"name": account["name"], "websockets": True}, method="PATCH")
        count += 1
    print(f"Upstream WebSockets enabled for {count} Codex account(s).")


def resolve_executable(name):
    executable = shutil.which(name)
    if executable:
        return executable
    candidates = [
        USER_DIR / ".local/bin" / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
    ]
    if name == "grok":
        candidates.insert(0, USER_DIR / ".grok/bin/grok")
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise SystemExit(f"Install {name} on this machine first.")


def agy_settings_path():
    return USER_DIR / ".gemini/antigravity-cli/settings.json"


def build_generic_env(client, base_env=None):
    environment = dict(os.environ if base_env is None else base_env)
    base = client["base_url"].rstrip("/")
    api_key = client["api_key"]
    environment["CLIPROXY_BASE_URL"] = base
    environment["CLIPROXY_API_KEY"] = api_key
    environment["OPENAI_BASE_URL"] = base + "/v1"
    environment["OPENAI_API_KEY"] = api_key
    environment["ANTHROPIC_BASE_URL"] = base
    environment["ANTHROPIC_AUTH_TOKEN"] = api_key
    environment.pop("ANTHROPIC_API_KEY", None)
    environment["GOOGLE_GEMINI_BASE_URL"] = base
    environment["GEMINI_API_KEY"] = api_key
    environment.pop("GOOGLE_API_KEY", None)
    environment.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
    environment["XAI_API_KEY"] = api_key
    environment["XAI_API_BASE_URL"] = base + "/v1"
    environment["GROK_XAI_API_BASE_URL"] = base + "/v1"
    environment["GROK_MODELS_BASE_URL"] = base + "/v1"
    environment["KIMI_BASE_URL"] = base + "/v1"
    environment["KIMI_API_KEY"] = api_key
    environment["META_API_BASE_URL"] = base + "/v1"
    environment["META_API_KEY"] = api_key
    environment["MODEL_API_KEY"] = api_key
    return environment


def build_client_env(name, client, base_env=None):
    environment = dict(os.environ if base_env is None else base_env)
    base = client["base_url"].rstrip("/")
    api_key = client["api_key"]
    if name == "claude":
        environment["CLAUDE_CONFIG_DIR"] = str(USER_DIR / ".claude-hub")
        environment["ANTHROPIC_BASE_URL"] = base
        environment["ANTHROPIC_AUTH_TOKEN"] = api_key
        environment.pop("ANTHROPIC_API_KEY", None)
    elif name == "codex":
        environment["CODEX_HOME"] = str(USER_DIR / ".codex-hub")
        environment["CLIPROXY_API_KEY"] = api_key
    elif name == "grok":
        grok_home = USER_DIR / ".grok-hub"
        grok_home.mkdir(mode=0o700, exist_ok=True)
        environment["GROK_HOME"] = str(grok_home)
        environment["XAI_API_KEY"] = api_key
        environment["XAI_API_BASE_URL"] = base + "/v1"
        environment["GROK_XAI_API_BASE_URL"] = base + "/v1"
        environment["GROK_MODELS_BASE_URL"] = base + "/v1"
        environment.setdefault("GROK_DEFAULT_MODEL", "grok-4.7")
        for conflict in ("GROK_AUTH", "GROK_AUTH_PATH", "GROK_DEPLOYMENT_KEY", "GROK_CODE_XAI_API_KEY"):
            environment.pop(conflict, None)
    elif name == "gemini":
        gemini_home = USER_DIR / ".gemini-hub"
        (gemini_home / ".gemini").mkdir(parents=True, mode=0o700, exist_ok=True)
        gemini_home.chmod(0o700)
        environment["GEMINI_CLI_HOME"] = str(gemini_home)
        environment["GOOGLE_GEMINI_BASE_URL"] = base
        environment["GEMINI_API_KEY"] = api_key
        environment.pop("GOOGLE_API_KEY", None)
        environment.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
    elif name == "agy":
        environment["GOOGLE_GEMINI_BASE_URL"] = base
        environment["GEMINI_API_KEY"] = api_key
        environment.pop("GOOGLE_API_KEY", None)
        environment.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
        environment.pop("AGY_ADC_AUTH", None)
    elif name == "kimi":
        kimi_home = USER_DIR / ".kimi-hub"
        kimi_home.mkdir(mode=0o700, exist_ok=True)
        environment["KIMI_CODE_HOME"] = str(kimi_home)
        environment["KIMI_SHARE_DIR"] = str(kimi_home)
        environment["KIMI_API_KEY"] = api_key
        environment["KIMI_BASE_URL"] = base + "/v1"
    elif name == "opencode":
        opencode_home = USER_DIR / ".opencode-hub"
        opencode_home.mkdir(mode=0o700, exist_ok=True)
        environment["OPENCODE_CONFIG_DIR"] = str(opencode_home)
        environment["OPENCODE_CONFIG"] = str(opencode_home / "opencode.json")
        environment["CLIPROXY_API_KEY"] = api_key
    elif name == "muse":
        environment["META_API_KEY"] = api_key
    else:
        raise ValueError(f"unsupported client launcher: {name}")
    return environment


def build_muse_args(client, arguments):
    args = list(arguments)
    if any("endpoint_transport.base_url" in arg for arg in args):
        return args
    base = client["base_url"].rstrip("/")
    endpoint_args = [
        "-c",
        "endpoint_transport.base_url=" + json.dumps(base + "/v1"),
        "-c",
        'endpoint_transport.auth="bearer"',
    ]
    if not args:
        return endpoint_args
    first = args[0]
    if first in ("-h", "--help", "-V", "--version") or first in MUSE_NON_SESSION_SUBCOMMANDS:
        return args
    if first in MUSE_SESSION_SUBCOMMANDS:
        if len(args) >= 2 and args[1] in ("-h", "--help"):
            return args
        return [first, *endpoint_args, *args[1:]]
    return [*endpoint_args, *args]


def launch_client(name, arguments):
    client = load("client.json")
    if name == "agy":
        settings = load_json_object(agy_settings_path())
        if settings.get("modelProvider") != "gemini":
            raise SystemExit(
                "Antigravity CLI is not in hub mode yet. Run `hubctl agy-mode hub` first "
                "(or `hubctl agy-mode account` later to restore Google sign-in)."
            )
    executable = resolve_executable(name)
    environment = build_client_env(name, client)
    if name == "muse":
        arguments = build_muse_args(client, arguments)
    os.execve(executable, [executable, *arguments], environment)


def classify_opencode_provider(model_id, owned_by):
    owner = (owned_by or "").strip().lower()
    lower = model_id.lower()
    if owner == "anthropic" or lower.startswith("claude-"):
        return ("cliproxy-claude", "@ai-sdk/anthropic", "Mini hub (Claude)", "/v1")
    if owner in ("google", "vertex") or lower.startswith("gemini-"):
        return ("cliproxy-gemini", "@ai-sdk/google", "Mini hub (Antigravity / Gemini)", "/v1beta")
    if owner == "antigravity":
        return ("cliproxy-antigravity", "@ai-sdk/openai-compatible", "Mini hub (Antigravity)", "/v1")
    if owner == "openai" or lower.startswith(("gpt-", "o1", "o3", "o4", "codex-")):
        return ("cliproxy-codex", "@ai-sdk/openai", "Mini hub (Codex)", "/v1")
    if owner == "xai" or lower.startswith("grok-"):
        return ("cliproxy-xai", "@ai-sdk/openai", "Mini hub (xAI / Grok)", "/v1")
    if owner in ("moonshot", "kimi", "kimi-ai") or lower.startswith("kimi-"):
        return ("cliproxy-kimi", "@ai-sdk/openai-compatible", "Mini hub (Kimi)", "/v1")
    if owner == "meta" or lower.startswith("muse-"):
        return ("cliproxy-meta", "@ai-sdk/openai", "Mini hub (Meta / Muse)", "/v1")
    if owner == "copilot" or lower.startswith("copilot/"):
        return ("cliproxy-copilot", "@ai-sdk/openai-compatible", "Mini hub (Copilot)", "/v1")
    if owner == "devin" or lower.startswith("devin/"):
        return ("cliproxy-devin", "@ai-sdk/openai-compatible", "Mini hub (Devin)", "/v1")
    slug = re.sub(r"[^a-z0-9-]+", "-", owner).strip("-") or "other"
    label = owner or "Other"
    return (f"cliproxy-{slug}", "@ai-sdk/openai-compatible", f"Mini hub ({label})", "/v1")


def is_chat_model(model_id):
    return not NON_CHAT_MODEL_RE.search(model_id)


def build_opencode_providers(base_url, models_data):
    base = base_url.rstrip("/")
    providers = {}
    for entry in sorted(models_data, key=lambda item: item.get("id", "")):
        model_id = (entry.get("id") or "").strip()
        if not model_id or not is_chat_model(model_id):
            continue
        key, npm, display_name, suffix = classify_opencode_provider(model_id, entry.get("owned_by", ""))
        provider = providers.setdefault(key, {
            "npm": npm,
            "name": display_name,
            "options": {
                "baseURL": base + suffix,
                "apiKey": "{env:CLIPROXY_API_KEY}",
            },
            "models": {},
        })
        provider["models"][model_id] = {"name": model_id}
    return providers


def update_codex_base_url(path, target_base_url):
    if not path.exists():
        return False
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    in_cliproxy = False
    changed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_cliproxy = stripped == "[model_providers.cliproxy]"
            continue
        if in_cliproxy and re.match(r"^base_url\s*=", stripped):
            ending = "\n" if line.endswith("\n") else ""
            replacement = "base_url = " + json.dumps(target_base_url) + ending
            if line != replacement:
                lines[index] = replacement
                changed = True
            break
    if changed:
        write_private_text(path, "".join(lines))
    return changed


def render_kimi_config(base_url, model_ids):
    models = list(model_ids)
    preferred = ("kimi-k3.1-pro", "kimi-k3", "kimi-k2.8-code", "kimi-k2.5")
    default_model = next((m for m in preferred if m in models), models[0])
    lines = [
        "# Isolated CLIProxyAPI client; credentials come from kimi-hub.",
        "default_model = " + json.dumps(default_model),
        "",
        "[providers.cliproxy]",
        'type = "openai"',
        "base_url = " + json.dumps(base_url.rstrip("/") + "/v1"),
        'api_key_env = "KIMI_API_KEY"',
    ]
    for model_id in models:
        lines.extend([
            "",
            f"[models.{json.dumps(model_id)}]",
            'provider = "cliproxy"',
            "model = " + json.dumps(model_id),
            "max_context_size = 262144",
        ])
    lines.append("")
    return "\n".join(lines)


def merge_kimi_config(path, base_url, model_ids):
    if not model_ids:
        return False
    if not path.exists():
        write_private_text(path, render_kimi_config(base_url, model_ids))
        return True
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    target_url = base_url.rstrip("/") + "/v1"
    in_cliproxy = False
    has_cliproxy = False
    changed = False
    existing_models = set()
    output_lines = []
    current_model_is_cliproxy = False
    current_model_has_ctx = False

    def flush_model_ctx():
        nonlocal changed, current_model_is_cliproxy, current_model_has_ctx
        if current_model_is_cliproxy and not current_model_has_ctx:
            insert_at = len(output_lines)
            while insert_at > 0 and not output_lines[insert_at - 1].strip():
                insert_at -= 1
            if insert_at > 0 and not output_lines[insert_at - 1].endswith("\n"):
                output_lines[insert_at - 1] += "\n"
            output_lines.insert(insert_at, "max_context_size = 262144\n")
            changed = True
        current_model_is_cliproxy = False
        current_model_has_ctx = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            flush_model_ctx()
            in_cliproxy = stripped == "[providers.cliproxy]"
            if in_cliproxy:
                has_cliproxy = True
            model_match = re.match(r'^\[models\.(?:"([^"]+)"|([A-Za-z0-9_.-]+))\]$', stripped)
            if model_match:
                existing_models.add(model_match.group(1) or model_match.group(2))
            output_lines.append(line)
            continue
        if in_cliproxy and re.match(r"^base_url\s*=", stripped):
            ending = "\n" if line.endswith("\n") else ""
            replacement = "base_url = " + json.dumps(target_url) + ending
            if line != replacement:
                line = replacement
                changed = True
        elif in_cliproxy and re.match(r'^type\s*=\s*"openai_legacy"$', stripped):
            ending = "\n" if line.endswith("\n") else ""
            line = 'type = "openai"' + ending
            changed = True
        elif re.match(r'^provider\s*=\s*"cliproxy"$', stripped):
            current_model_is_cliproxy = True
        elif re.match(r"^max_context_size\s*=", stripped):
            current_model_has_ctx = True
        output_lines.append(line)
    flush_model_ctx()

    additions = []
    if not has_cliproxy:
        additions.append("\n".join([
            "[providers.cliproxy]",
            'type = "openai"',
            "base_url = " + json.dumps(target_url),
            'api_key_env = "KIMI_API_KEY"',
        ]))
    for model_id in model_ids:
        if model_id in existing_models:
            continue
        additions.append("\n".join([
            f"[models.{json.dumps(model_id)}]",
            'provider = "cliproxy"',
            "model = " + json.dumps(model_id),
            "max_context_size = 262144",
        ]))
    if not changed and not additions:
        return False
    text = "".join(output_lines)
    if additions:
        if text and not text.endswith("\n"):
            text += "\n"
        if text:
            text += "\n"
        text += "\n\n".join(additions) + "\n"
    write_private_text(path, text)
    return True


def merge_toml_mcp_servers(path, servers, include_enabled=False, header=None):
    existing_text = path.read_text() if path.exists() else ""
    existing_names = set()
    for line in existing_text.splitlines():
        match = TOML_MCP_SECTION_RE.match(line.strip())
        if match:
            existing_names.add(match.group(1) or match.group(2))
    additions = []
    for name in sorted(servers):
        if name in existing_names:
            continue
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
        additions.append("\n".join(lines))
    if not additions:
        return 0
    prefix = existing_text
    if not prefix and header:
        prefix = header.rstrip("\n") + "\n"
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix:
        prefix += "\n"
    write_private_text(path, prefix + "\n\n".join(additions) + "\n")
    return len(additions)


def merge_json_mcp_servers(path, servers, root_key, format_entry, skip_names=(), defaults=None):
    existing = load_json_object(path)
    changed = False
    if defaults:
        for key, value in defaults.items():
            if key not in existing:
                existing[key] = value
                changed = True
    target = existing.get(root_key)
    if not isinstance(target, dict):
        target = {}
        existing[root_key] = target
    skip = set(skip_names)
    added = 0
    for name in sorted(servers):
        if name in target or name in skip:
            continue
        spec = servers[name]
        if not isinstance(spec, dict):
            continue
        entry = format_entry(spec)
        if entry is not None:
            target[name] = entry
            added += 1
    if added or changed or not path.exists():
        write_private(path, existing)
    return added


def format_claude_mcp_entry(spec):
    if spec.get("url"):
        return {"type": "http", "url": spec["url"]}
    if spec.get("command"):
        return {"type": "stdio", "command": spec["command"], "args": list(spec.get("args", []))}
    return None


def format_gemini_mcp_entry(spec):
    if spec.get("url"):
        return {"httpUrl": spec["url"]}
    if spec.get("command"):
        entry = {"command": spec["command"]}
        if spec.get("args"):
            entry["args"] = list(spec["args"])
        return entry
    return None


def format_agy_mcp_entry(spec):
    if spec.get("url"):
        return {"serverUrl": spec["url"]}
    if spec.get("command"):
        entry = {"command": spec["command"]}
        if spec.get("args"):
            entry["args"] = list(spec["args"])
        return entry
    return None


def format_kimi_mcp_entry(spec):
    if spec.get("url"):
        return {"url": spec["url"]}
    if spec.get("command"):
        entry = {"command": spec["command"]}
        if spec.get("args"):
            entry["args"] = list(spec["args"])
        return entry
    return None


def format_opencode_mcp_entry(spec):
    if spec.get("url"):
        return {"type": "remote", "url": spec["url"], "enabled": True}
    if spec.get("command"):
        return {"type": "local", "command": [spec["command"], *spec.get("args", [])], "enabled": True}
    return None


def format_muse_mcp_entry(spec):
    if spec.get("url"):
        return {"type": "streamable-http", "url": spec["url"]}
    if spec.get("command"):
        return {"type": "stdio", "command": spec["command"], "args": list(spec.get("args", []))}
    return None


def claude_enabled_plugin_names():
    settings = load_json_object(USER_DIR / ".claude-hub/settings.json")
    return {
        plugin_id.split("@", 1)[0]
        for plugin_id, enabled in (settings.get("enabledPlugins") or {}).items()
        if enabled
    }


def sync_mcp_servers(servers):
    if not servers:
        return {}
    counts = {}
    counts["claude-hub"] = merge_json_mcp_servers(
        USER_DIR / ".claude-hub/.claude.json",
        servers,
        "mcpServers",
        format_claude_mcp_entry,
        skip_names=claude_enabled_plugin_names(),
    )
    counts["codex-hub"] = merge_toml_mcp_servers(
        USER_DIR / ".codex-hub/config.toml",
        servers,
        include_enabled=False,
    )
    counts["grok-hub"] = merge_toml_mcp_servers(
        USER_DIR / ".grok-hub/config.toml",
        servers,
        include_enabled=True,
        header="# Isolated CLIProxyAPI client; credentials come from grok-hub.",
    )
    counts["gemini-hub"] = merge_json_mcp_servers(
        USER_DIR / ".gemini-hub/.gemini/settings.json",
        servers,
        "mcpServers",
        format_gemini_mcp_entry,
    )
    counts["kimi-hub"] = merge_json_mcp_servers(
        USER_DIR / ".kimi-hub/mcp.json",
        servers,
        "mcpServers",
        format_kimi_mcp_entry,
    )
    counts["opencode-hub"] = merge_json_mcp_servers(
        USER_DIR / ".opencode-hub/opencode.json",
        servers,
        "mcp",
        format_opencode_mcp_entry,
    )
    counts["agy"] = merge_json_mcp_servers(
        USER_DIR / ".gemini/config/mcp_config.json",
        servers,
        "mcpServers",
        format_agy_mcp_entry,
    )
    counts["muse"] = merge_json_mcp_servers(
        USER_DIR / ".config/muse/settings.json",
        servers,
        "mcpServers",
        format_muse_mcp_entry,
        defaults={"schema_version": 1},
    )
    return counts


def sync_clients(client):
    models_resp = request("/v1/models", client["api_key"], client["base_url"])
    models_data = models_resp.get("data", [])
    base = client["base_url"].rstrip("/")

    # OpenCode: refresh all cliproxy-* providers while preserving custom non-hub providers.
    opencode_path = USER_DIR / ".opencode-hub/opencode.json"
    existing = load_json_object(opencode_path)
    preserved = {
        k: v for k, v in existing.get("provider", {}).items()
        if not k.startswith("cliproxy-")
    }
    generated = build_opencode_providers(base, models_data)
    preserved.update(generated)
    existing["$schema"] = existing.get("$schema", "https://opencode.ai/config.json")
    existing["provider"] = preserved
    write_private(opencode_path, existing)

    # Kimi: non-destructively extend all live hub chat models and keep cliproxy config in sync.
    kimi_models = sorted({
        entry["id"] for entry in models_data
        if entry.get("id") and is_chat_model(entry["id"])
    })
    kimi_updated = merge_kimi_config(USER_DIR / ".kimi-hub/config.toml", base, kimi_models)

    # Gemini: ensure API-key auth mode file exists while preserving mcpServers/settings.
    gemini_settings_path = USER_DIR / ".gemini-hub/.gemini/settings.json"
    gemini_settings = load_json_object(gemini_settings_path)
    security = gemini_settings.setdefault("security", {})
    auth = security.setdefault("auth", {})
    if auth.get("selectedType") != "gemini-api-key" or not gemini_settings_path.exists():
        auth["selectedType"] = "gemini-api-key"
        write_private(gemini_settings_path, gemini_settings)

    # Codex: keep base_url in sync with client.json without touching user sections.
    codex_updated = update_codex_base_url(USER_DIR / ".codex-hub/config.toml", base + "/v1")

    # Sync default MCP servers non-destructively across all client CLIs (including Antigravity).
    mcp_servers = load_mcp_servers()
    mcp_counts = sync_mcp_servers(mcp_servers)

    total_opencode_models = sum(len(p["models"]) for p in generated.values())
    print(f"Synced OpenCode ({len(generated)} provider groups, {total_opencode_models} models): {opencode_path}")
    if kimi_updated:
        print(f"Synced Kimi ({len(kimi_models)} models): {USER_DIR / '.kimi-hub/config.toml'}")
    if codex_updated:
        print(f"Updated Codex base_url: {USER_DIR / '.codex-hub/config.toml'}")
    if mcp_servers:
        added_summary = ", ".join(f"{name}=+{count}" for name, count in sorted(mcp_counts.items()) if count)
        if added_summary:
            print(f"Synced default MCP servers ({len(mcp_servers)} configured): {added_summary}")
        else:
            print(f"Default MCP servers already synced ({len(mcp_servers)} configured across all clients)")


def set_agy_mode(mode):
    path = agy_settings_path()
    settings = load_json_object(path)
    if mode == "status":
        current = "hub (modelProvider=gemini)" if settings.get("modelProvider") == "gemini" else "account (Google sign-in)"
        print(f"Antigravity CLI mode: {current}")
        print(f"Settings: {path}")
        return
    if mode == "hub":
        settings["modelProvider"] = "gemini"
        write_private(path, settings)
        mcp_servers = load_mcp_servers()
        if mcp_servers:
            merge_json_mcp_servers(
                USER_DIR / ".gemini/config/mcp_config.json",
                mcp_servers,
                "mcpServers",
                format_agy_mcp_entry,
            )
        print(f"Antigravity CLI set to hub mode (`modelProvider=gemini`): {path}")
        print("Launch with `agy-hub` (note: plain `agy` requires GEMINI_API_KEY while hub mode is active).")
    elif mode == "account":
        if "modelProvider" in settings:
            settings.pop("modelProvider", None)
            write_private(path, settings)
        print(f"Antigravity CLI set to Google account sign-in: {path}")


def validate_client_name(name):
    if not CLIENT_NAME_RE.match(name):
        raise SystemExit("Client name must be 1-64 characters of letters, digits, '.', '_', or '-'.")
    return name


def update_hub_api_keys(credentials, transform):
    current, headers = request_with_headers("/v8/management/config/access/api-keys", credentials["management_key"])
    existing = list(current) if isinstance(current, list) else []
    updated = transform(existing)
    if updated == existing:
        return updated
    etag = headers.get("ETag")
    extra = {"If-Match": etag} if etag else None
    request_with_headers(
        "/v8/management/config/access/api-keys",
        credentials["management_key"],
        payload=updated,
        method="PUT",
        extra_headers=extra,
    )
    return updated


def ensure_named_client_key(credentials, name):
    validate_client_name(name)
    client_keys = dict(credentials.get("client_keys") or {})
    key = client_keys.get(name) or secrets.token_urlsafe(48)

    def add_key(existing):
        result = []
        for item in [credentials["api_key"], *existing, key]:
            if item and item not in result:
                result.append(item)
        return result

    update_hub_api_keys(credentials, add_key)
    client_keys[name] = key
    credentials["client_keys"] = client_keys
    write_private(CONFIG / "secrets.json", credentials)
    return key


def revoke_named_client_key(credentials, name):
    validate_client_name(name)
    client_keys = dict(credentials.get("client_keys") or {})
    if name not in client_keys:
        raise SystemExit(f"Unknown client name: {name}")
    target_key = client_keys[name]
    if target_key == credentials.get("api_key"):
        raise SystemExit("Refusing to revoke the primary hub API key.")

    def remove_key(existing):
        result = [item for item in existing if item != target_key]
        if not result and credentials.get("api_key"):
            result.append(credentials["api_key"])
        return result

    update_hub_api_keys(credentials, remove_key)
    client_keys.pop(name, None)
    credentials["client_keys"] = client_keys
    write_private(CONFIG / "secrets.json", credentials)
    print(f"Revoked client key for {name}.")


def list_named_clients(credentials):
    client_keys = credentials.get("client_keys") or {}
    if not client_keys:
        print("Named clients: none (use `hubctl export-client <dir> --name <machine>` to issue per-machine keys)")
        return
    active = set(request("/v8/management/config/access/api-keys", credentials["management_key"]) or [])
    for name in sorted(client_keys):
        state = "active" if client_keys[name] in active else "inactive"
        print(f"Client: {name} ({state})")


def list_default_mcp():
    servers = load_mcp_servers()
    if not servers:
        print("Default MCP servers: none")
        return
    for name in sorted(servers):
        spec = servers[name]
        if not isinstance(spec, dict):
            continue
        if spec.get("url"):
            print(f"{name}: http {spec['url']}")
        elif spec.get("command"):
            cmd = " ".join([spec["command"], *spec.get("args", [])])
            print(f"{name}: stdio {cmd}")


def main():
    invoked = Path(sys.argv[0]).name
    if invoked in WRAPPER_NAMES:
        launch_client(invoked.removesuffix("-hub"), sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for command in ("status", "serve", "restart", "start", "stop", "copy-api-key", "copy-management-key", "enable-websockets", "sync-clients", "clients", "mcp"):
        sub.add_parser(command)

    agy_cmd = sub.add_parser("agy-mode", help="switch Antigravity CLI between Mini hub mode and Google sign-in")
    agy_cmd.add_argument("mode", nargs="?", choices=("status", "hub", "account"), default="status")

    models_cmd = sub.add_parser("models")
    models_cmd.add_argument("--provider", help="filter models by owner or ID prefix (e.g. claude, codex, gemini, grok, kimi, meta)")
    models_cmd.add_argument("--summary", action="store_true", help="show model counts grouped by provider owner")

    login = sub.add_parser("login")
    login.add_argument("provider", choices=tuple(LOGIN_PROVIDERS))
    login.add_argument("--browser", action="store_true", help="use browser OAuth instead of device flow (Codex)")
    login.add_argument("--no-browser", action="store_true", help="print the OAuth URL instead of opening a browser")
    login.add_argument("--oauth-callback-port", type=int, default=0, help="override local OAuth callback port")
    login.add_argument("--login-proxy", default="", help="proxy URL used for OAuth login and saved to the credential")

    vertex = sub.add_parser("import-vertex")
    vertex.add_argument("key_file", type=Path, help="Google Cloud service account JSON path")
    vertex.add_argument("--prefix", default="", help="optional model prefix for the imported Vertex credential")

    export = sub.add_parser("export-client")
    export.add_argument("directory", type=Path)
    export.add_argument("--name", help="issue or reuse a dedicated access key for this machine")

    revoke = sub.add_parser("revoke-client")
    revoke.add_argument("name", help="named client to revoke from access.api-keys")

    run_cmd = sub.add_parser("run", help="run any command with hub environment variables for all providers")
    run_cmd.add_argument("command", nargs=argparse.REMAINDER, help="command and arguments to run")

    args = parser.parse_args()

    if args.action == "mcp":
        list_default_mcp()
        return

    if args.action == "agy-mode":
        set_agy_mode(args.mode)
        return

    if args.action == "models":
        client = load("client.json")
        models = request("/v1/models", client["api_key"], client["base_url"]).get("data", [])
        if args.provider:
            needle = args.provider.strip().lower()
            aliases = {
                "claude": ("anthropic", "claude-"),
                "codex": ("openai", "gpt-", "codex-"),
                "openai": ("openai", "gpt-", "codex-"),
                "gemini": ("antigravity", "google", "vertex", "gemini-"),
                "antigravity": ("antigravity", "gemini-"),
                "xai": ("xai", "grok-"),
                "grok": ("xai", "grok-"),
                "kimi": ("moonshot", "kimi", "kimi-ai", "kimi-"),
                "kimi-ai": ("moonshot", "kimi", "kimi-ai", "kimi-"),
                "meta": ("meta", "muse-"),
                "muse": ("meta", "muse-"),
            }
            terms = aliases.get(needle, (needle,))
            models = [
                item for item in models
                if (item.get("owned_by") or "").strip().lower() in terms
                or any((item.get("id") or "").lower().startswith(term) for term in terms)
            ]
        if args.summary:
            counts = Counter((item.get("owned_by") or "unknown").strip().lower() for item in models)
            for owner, count in sorted(counts.items()):
                print(f"{owner}: {count}")
            return
        for model in sorted(models, key=lambda item: item.get("id", "")):
            print(model["id"])
        return

    if args.action == "sync-clients":
        sync_clients(load("client.json"))
        return

    if args.action == "run":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            parser.error("hubctl run requires a command")
        client = load("client.json")
        executable = shutil.which(command[0]) or (command[0] if os.path.sep in command[0] and os.access(command[0], os.X_OK) else None)
        if not executable:
            raise SystemExit(f"Command not found: {command[0]}")
        environment = build_generic_env(client)
        os.execve(executable, [executable, *command[1:]], environment)

    credentials = load("secrets.json") if (CONFIG / "secrets.json").exists() else {}

    if args.action == "status":
        client = load("client.json")
        models = request("/v1/models", client["api_key"], client["base_url"]).get("data", [])
        print("Endpoint:", client["base_url"])
        if client.get("client_name"):
            print("Client:", client["client_name"])
        print("Models:", len(models))
        owner_counts = Counter((item.get("owned_by") or "unknown").strip().lower() for item in models)
        if owner_counts:
            summary = ", ".join(f"{owner}={count}" for owner, count in sorted(owner_counts.items()))
            print("Model providers:", summary)
        if credentials:
            accounts = request("/v8/management/credentials", credentials["management_key"])
            counts = Counter((item.get("provider") or item.get("type") or "unknown", "disabled" if item.get("disabled") else "enabled") for item in accounts.get("files", []))
            for (provider, state), count in sorted(counts.items()):
                print(f"Accounts: {provider} {state}={count}")
            if not counts:
                print("Accounts: none; run hubctl login <provider>")
            named = credentials.get("client_keys") or {}
            if named:
                print("Named clients:", ", ".join(sorted(named)))
            if credentials.get("remote_url"):
                print("Private remote endpoint:", credentials["remote_url"])
        return

    if not credentials:
        parser.error("this command runs on the Mini hub")

    if args.action == "login":
        # Fresh OAuth grants avoid sharing refresh tokens with the normal CLIs.
        if args.browser and args.provider != "codex":
            parser.error("--browser is only supported for codex")
        flag = "--codex-login" if (args.provider == "codex" and args.browser) else LOGIN_PROVIDERS[args.provider]
        os.chdir(STATE)
        binary = str(STATE / "bin/cli-proxy-api")
        command = [binary, "--config", str(CONFIG / "config.yaml"), flag]
        if args.no_browser:
            command.append("--no-browser")
        if args.oauth_callback_port > 0:
            command.extend(["--oauth-callback-port", str(args.oauth_callback_port)])
        if args.login_proxy:
            command.extend(["--login-proxy", args.login_proxy])
        subprocess.run(command, check=True)
        if args.provider == "codex":
            enable_websockets(credentials)
    elif args.action == "import-vertex":
        key_file = args.key_file.expanduser().resolve()
        if not key_file.is_file():
            raise SystemExit(f"Service account file not found: {key_file}")
        os.chdir(STATE)
        binary = str(STATE / "bin/cli-proxy-api")
        command = [binary, "--config", str(CONFIG / "config.yaml"), "--vertex-import", str(key_file)]
        if args.prefix:
            command.extend(["--vertex-import-prefix", args.prefix])
        subprocess.run(command, check=True)
    elif args.action == "enable-websockets":
        enable_websockets(credentials)
    elif args.action == "clients":
        list_named_clients(credentials)
    elif args.action == "revoke-client":
        revoke_named_client_key(credentials, args.name)
    elif args.action.startswith("copy-"):
        key = "api_key" if args.action == "copy-api-key" else "management_key"
        if not shutil.which("pbcopy"):
            raise SystemExit("Clipboard commands require macOS.")
        subprocess.run(["pbcopy"], input=credentials[key].encode(), check=True)
        print("Copied to clipboard.")
    elif args.action in ("start", "stop", "restart"):
        domain = "gui/" + str(os.getuid())
        if args.action == "restart":
            subprocess.run(["launchctl", "kickstart", "-k", domain + "/" + LABEL], check=True)
        elif args.action == "stop":
            subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], check=True)
        else:
            subprocess.run(["launchctl", "bootstrap", domain, str(USER_DIR / "Library/LaunchAgents" / (LABEL + ".plist"))], check=True)
    elif args.action == "serve":
        cli = tailscale_binary()
        state = json.loads(subprocess.check_output([cli, "status", "--json"]))
        if state.get("BackendState") != "Running":
            raise SystemExit("Sign in to Tailscale on the Mini first.")
        subprocess.run([cli, "serve", "--bg", "--https=8317", LOCAL_URL], check=True)
        hostname = state["Self"]["DNSName"].rstrip(".")
        credentials["remote_url"] = "https://" + hostname + ":8317"
        write_private(CONFIG / "secrets.json", credentials)
        print("Private remote endpoint:", credentials["remote_url"])
    elif args.action == "export-client":
        if not credentials.get("remote_url"):
            raise SystemExit("Run hubctl serve first.")
        destination = args.directory.expanduser().resolve()
        if destination.exists():
            raise SystemExit(f"Destination already exists: {destination}")
        if args.name:
            api_key = ensure_named_client_key(credentials, args.name)
            payload = {"base_url": credentials["remote_url"], "api_key": api_key, "client_name": args.name}
        else:
            payload = {"base_url": credentials["remote_url"], "api_key": credentials["api_key"]}
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name in ("install.py", "hubctl.py"):
            shutil.copy2(STATE / name, destination / name)
            (destination / name).chmod(0o700)
        for mcp_source in (CONFIG / "mcp-servers.json", STATE / "mcp-servers.json"):
            if mcp_source.exists():
                shutil.copy2(mcp_source, destination / "mcp-servers.json")
                (destination / "mcp-servers.json").chmod(0o600)
                break
        write_private(destination / "client.json", payload)
        print("Private client bundle:", destination)
        if args.name:
            print("Dedicated client key registered for:", args.name)
        print("On your other machine: python3 install.py --client && hubctl sync-clients")
        print("Contains the access key; keep this bundle private.")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except (OSError, ValueError, urllib.error.URLError, subprocess.CalledProcessError) as error:
        # HTTPError.__str__ contains only the status, never request headers.
        print("hubctl:", error, file=sys.stderr)
        raise SystemExit(1)
