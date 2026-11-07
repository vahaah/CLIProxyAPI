import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("mini_installer", ROOT / "scripts/mini-hub/install.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class UpstreamSync(unittest.TestCase):
    def install_existing(self, build_fails):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".config/cliproxyapi-mini"
            state = home / ".local/share/cliproxyapi-mini"
            commands = home / ".local/bin"
            config.mkdir(parents=True)
            binary = state / "bin/cli-proxy-api"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"old proxy")
            private_files = {
                config / "config.yaml": b"existing server configuration\n",
                config / "secrets.json": json.dumps({"api_key": "fixture-api", "management_key": "fixture-management"}).encode(),
                config / "static/management.html": b"existing panel",
                home / ".claude-hub/settings.json": b'{"existing":true}',
                home / ".codex-hub/config.toml": b'model = "existing"\n',
                home / ".cli-proxy-api/account.json": b"existing OAuth grant",
            }
            for path, content in private_files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            client_path = config / "client.json"
            existing_client = {"base_url": "https://existing-private-hub.test:8317", "api_key": "existing-client-key"}
            client_path.write_text(json.dumps(existing_client))
            processes = []

            def run(arguments, **kwargs):
                processes.append(arguments)
                if arguments[0] == "go":
                    if build_fails:
                        raise subprocess.CalledProcessError(1, arguments)
                    Path(arguments[arguments.index("-o") + 1]).write_bytes(b"new proxy")
                return subprocess.CompletedProcess(arguments, 0)

            def check_output(arguments, **kwargs):
                if arguments[0] == "/usr/bin/xcrun":
                    return "/fixture-sdk\n"
                if arguments[1] == "describe":
                    return "v8.0.59\n"
                return "fixture-commit\n"

            with patch.multiple(INSTALLER, USER_DIR=home, CONFIG=config, STATE=state, COMMANDS=commands), \
                    patch.object(INSTALLER.subprocess, "run", side_effect=run), \
                    patch.object(INSTALLER.subprocess, "check_output", side_effect=check_output), \
                    patch.object(INSTALLER, "install_tailscale_login"), \
                    patch.object(sys, "argv", ["install.py"]), patch.object(sys, "platform", "darwin"), \
                    contextlib.redirect_stdout(io.StringIO()):
                if build_fails:
                    with self.assertRaises(subprocess.CalledProcessError):
                        INSTALLER.main()
                else:
                    INSTALLER.main()

            for path, content in private_files.items():
                self.assertEqual(path.read_bytes(), content, str(path.relative_to(home)))
            if build_fails:
                self.assertEqual(binary.read_bytes(), b"old proxy")
                self.assertFalse(Path(str(binary) + ".previous").exists())
                self.assertEqual([command[0] for command in processes], ["go"])
                self.assertEqual(json.loads(client_path.read_text()), existing_client)
            else:
                self.assertEqual(binary.read_bytes(), b"new proxy")
                self.assertEqual(Path(str(binary) + ".previous").read_bytes(), b"old proxy")
                self.assertEqual(processes[-1], ["launchctl", "kickstart", "-k", "gui/" + str(INSTALLER.os.getuid()) + "/" + INSTALLER.LABEL])
                self.assertEqual(json.loads(client_path.read_text()), {"base_url": "http://127.0.0.1:8317", "api_key": "fixture-api"})

    def test_success_preserves_private_state_and_backup(self):
        """[control:sync-install]"""
        self.install_existing(build_fails=False)

    def test_build_failure_preserves_running_installation(self):
        """[control:sync-reject]"""
        self.install_existing(build_fails=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
