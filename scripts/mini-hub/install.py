#!/usr/bin/env python3
"""Install the native Mini hub, or an exported laptop client, without sudo."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.request


HERE = Path(__file__).resolve().parent
USER_DIR = Path.home()
CONFIG = USER_DIR / ".config/cliproxyapi-mini"
STATE = USER_DIR / ".local/share/cliproxyapi-mini"
COMMANDS = USER_DIR / ".local/bin"
LABEL = "com.vahaah.cliproxyapi-mini"
PANEL_URL = "https://github.com/router-for-me/Cli-Proxy-API-Management-Center/releases/download/v1.26.7/management.html"
PANEL_SHA256 = "8c9c6b7d3fb311e6aad5d06e1993e53a2d28389e640448a1bc6304ed20b057b5"


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


def install_clients(client):
    json_write(CONFIG / "client.json", client)
    # Isolate preferences and history; authentication is injected by the wrapper.
    claude_dir = USER_DIR / ".claude-hub"
    claude_dir.mkdir(mode=0o700, exist_ok=True)
    claude_settings = claude_dir / "settings.json"
    if not claude_settings.exists():
        original = USER_DIR / ".claude/settings.json"
        settings = json.loads(original.read_text()) if original.exists() else {}
        for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
            settings.get("env", {}).pop(key, None)
        json_write(claude_settings, settings)
    codex_dir = USER_DIR / ".codex-hub"
    codex_dir.mkdir(mode=0o700, exist_ok=True)
    codex_settings = codex_dir / "config.toml"
    if not codex_settings.exists():
        private_write(codex_settings, '\n'.join([
            '# Isolated CLIProxyAPI client; credentials come from codex-hub.',
            'model_provider = "cliproxy"',
            '',
            '[model_providers.cliproxy]',
            'name = "Mini hub"',
            'base_url = ' + json.dumps(client["base_url"] + "/v1"),
            'env_key = "CLIPROXY_API_KEY"',
            'wire_api = "responses"',
            'requires_openai_auth = false',
            'supports_websockets = true',
            # The hub already owns credential retries. Avoid multiplying them.
            'request_max_retries = 1',
            'stream_max_retries = 1',
            '',
        ]))
    for name in ("hubctl", "claude-hub", "codex-hub"):
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
    parser.add_argument("--client", action="store_true", help="install an exported laptop client")
    parser.add_argument("--no-start", action="store_true", help="prepare service without loading launchd")
    args = parser.parse_args()
    if sys.platform != "darwin" and not args.client:
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
    if args.client:
        install_clients(json.loads((HERE / "client.json").read_text()))
        print(f"Installed claude-hub, codex-hub and hubctl in {COMMANDS}")
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
    print("Commands: hubctl status, hubctl login claude, hubctl login codex, claude-hub, codex-hub")


if __name__ == "__main__":
    main()
