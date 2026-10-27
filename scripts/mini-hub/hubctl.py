#!/usr/bin/env python3
"""Private hub management and isolated Claude/Codex launchers. Never print keys."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


USER_DIR = Path.home()
CONFIG = USER_DIR / ".config/cliproxyapi-mini"
STATE = USER_DIR / ".local/share/cliproxyapi-mini"
LABEL = "com.vahaah.cliproxyapi-mini"
LOCAL_URL = "http://127.0.0.1:8317"


def load(name):
    return json.loads((CONFIG / name).read_text())


def write_private(path, value):
    # Reuse the installer's atomic writer; both files live outside the repo.
    sys.path.insert(0, str(STATE))
    from install import json_write
    json_write(path, value)


def tailscale_binary():
    for candidate in (shutil.which("tailscale"), "/usr/local/bin/tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("Install Tailscale and sign in on the Mini first.")


def request(path, key, base=LOCAL_URL, payload=None, method=None):
    headers = {"Authorization": "Bearer " + key}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, headers=headers, data=data, method=method)
    # Ignore any ambient outbound proxy for this private hub endpoint.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as response:
        return json.load(response)


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


def launch_client(name, arguments):
    client = load("client.json")
    executable = shutil.which(name)
    if not executable:
        raise SystemExit(f"Install {name} on this machine first.")
    environment = dict(os.environ)
    if name == "claude":
        environment["CLAUDE_CONFIG_DIR"] = str(USER_DIR / ".claude-hub")
        environment["ANTHROPIC_BASE_URL"] = client["base_url"]
        environment["ANTHROPIC_AUTH_TOKEN"] = client["api_key"]
        environment.pop("ANTHROPIC_API_KEY", None)
    else:
        environment["CODEX_HOME"] = str(USER_DIR / ".codex-hub")
        environment["CLIPROXY_API_KEY"] = client["api_key"]
    os.execve(executable, [executable, *arguments], environment)


def main():
    invoked = Path(sys.argv[0]).name
    if invoked in ("claude-hub", "codex-hub"):
        launch_client(invoked.removesuffix("-hub"), sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for command in ("status", "serve", "restart", "start", "stop", "copy-api-key", "copy-management-key", "enable-websockets"):
        sub.add_parser(command)
    sub.add_parser("models")
    login = sub.add_parser("login")
    login.add_argument("provider", choices=("claude", "codex"))
    export = sub.add_parser("export-client")
    export.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.action == "models":
        client = load("client.json")
        models = request("/v1/models", client["api_key"], client["base_url"])
        for model in models.get("data", []):
            print(model["id"])
        return
    credentials = load("secrets.json") if (CONFIG / "secrets.json").exists() else {}
    if args.action == "status":
        client = load("client.json")
        models = request("/v1/models", client["api_key"], client["base_url"])
        print("Endpoint:", client["base_url"])
        print("Models:", len(models.get("data", [])))
        if credentials:
            accounts = request("/v8/management/credentials", credentials["management_key"])
            counts = Counter((item.get("provider") or item.get("type") or "unknown", "disabled" if item.get("disabled") else "enabled") for item in accounts.get("files", []))
            for (provider, state), count in sorted(counts.items()):
                print(f"Accounts: {provider} {state}={count}")
            if not counts:
                print("Accounts: none; run hubctl login claude / hubctl login codex")
            if credentials.get("remote_url"):
                print("Private remote endpoint:", credentials["remote_url"])
        return
    if not credentials:
        parser.error("this command runs on the Mini hub")
    if args.action == "login":
        # Fresh OAuth grants avoid sharing refresh tokens with the normal CLI.
        flag = "--claude-login" if args.provider == "claude" else "--codex-device-login"
        os.chdir(STATE)
        binary = str(STATE / "bin/cli-proxy-api")
        subprocess.run([binary, "--config", str(CONFIG / "config.yaml"), flag], check=True)
        if args.provider == "codex":
            enable_websockets(credentials)
    elif args.action == "enable-websockets":
        enable_websockets(credentials)
    elif args.action.startswith("copy-"):
        key = "api_key" if args.action == "copy-api-key" else "management_key"
        if not shutil.which("pbcopy"):
            raise SystemExit("Clipboard commands require macOS.")
        subprocess.run(["pbcopy"], input=credentials[key].encode(), check=True)
        print("Copied to clipboard.")
    elif args.action in ("start", "stop", "restart"):
        domain = "gui/" + str(os.getuid())
        if args.action in ("stop", "restart"):
            subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], check=True)
        if args.action in ("start", "restart"):
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
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name in ("install.py", "hubctl.py"):
            shutil.copy2(STATE / name, destination / name)
            (destination / name).chmod(0o700)
        write_private(destination / "client.json", {"base_url": credentials["remote_url"], "api_key": credentials["api_key"]})
        print("Private client bundle:", destination)
        print("On your laptop: python3 install.py --client")
        print("Contains the access key; keep this bundle private.")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except (OSError, ValueError, urllib.error.URLError) as error:
        # HTTPError.__str__ contains only the status, never request headers.
        print("hubctl:", error, file=sys.stderr)
        raise SystemExit(1)
