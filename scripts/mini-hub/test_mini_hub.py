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


ROOT = Path(__file__).resolve().parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = load_module("mini_hub_install", "install.py")
HUBCTL = load_module("mini_hub_ctl", "hubctl.py")


SAMPLE_MODELS = {
    "data": [
        {"id": "claude-opus-5-5", "owned_by": "anthropic"},
        {"id": "claude-sonnet-5-5-high", "owned_by": "antigravity"},
        {"id": "gemini-4-argon-high", "owned_by": "antigravity"},
        {"id": "gemini-3.1-flash-image", "owned_by": "antigravity"},
        {"id": "gpt-6.1-sol", "owned_by": "openai"},
        {"id": "gpt-image-2.5", "owned_by": "openai"},
        {"id": "grok-4.7", "owned_by": "xai"},
        {"id": "grok-imagine-video-1.5", "owned_by": "xai"},
        {"id": "grok-tts", "owned_by": "xai"},
        {"id": "kimi-k3.1-pro", "owned_by": "moonshot"},
        {"id": "kimi-k2.5", "owned_by": "moonshot"},
        {"id": "muse-spark-1.3", "owned_by": "meta"},
        {"id": "muse-wonder-full-1.0", "owned_by": "meta"},
    ]
}


class MiniHubHelpersTest(unittest.TestCase):
    def test_client_install_creates_all_wrappers_and_default_mcps_without_plaintext_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            bundle = home / "bundle"
            bundle.mkdir()
            for filename in ("install.py", "hubctl.py", "mcp-servers.json"):
                (bundle / filename).write_text((ROOT / filename).read_text())
            secret_key = "secret-client-token-value"
            (bundle / "client.json").write_text(json.dumps({
                "base_url": "https://mini.tailnet.ts.net:8317",
                "api_key": secret_key,
                "client_name": "macbook",
            }))

            config = home / ".config/cliproxyapi-mini"
            state = home / ".local/share/cliproxyapi-mini"
            commands = home / ".local/bin"
            with patch.multiple(INSTALLER, HERE=bundle, USER_DIR=home, CONFIG=config, STATE=state, COMMANDS=commands), \
                    patch.object(sys, "argv", ["install.py", "--client"]), \
                    patch.object(sys, "platform", "linux"), \
                    contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.main()

            for name in INSTALLER.CLIENT_COMMANDS:
                link = commands / name
                self.assertTrue(link.is_symlink(), name)
                self.assertEqual(link.resolve(), (state / "hubctl.py").resolve())

            for cfg_path in (
                home / ".claude-hub/settings.json",
                home / ".claude-hub/.claude.json",
                home / ".codex-hub/config.toml",
                home / ".grok-hub/config.toml",
                home / ".gemini-hub/.gemini/settings.json",
                home / ".kimi-hub/config.toml",
                home / ".kimi-hub/mcp.json",
                home / ".opencode-hub/opencode.json",
                home / ".config/muse/settings.json",
            ):
                self.assertTrue(cfg_path.exists(), str(cfg_path))
                self.assertNotIn(secret_key, cfg_path.read_text(), str(cfg_path))
                self.assertEqual(cfg_path.stat().st_mode & 0o077, 0, str(cfg_path))

            muse_settings = json.loads((home / ".config/muse/settings.json").read_text())
            self.assertEqual(muse_settings["schema_version"], 1)
            self.assertEqual(muse_settings["mcpServers"]["linear"], {"type": "streamable-http", "url": "https://mcp.linear.app/mcp"})
            self.assertEqual(muse_settings["mcpServers"]["argent"], {"type": "stdio", "command": "argent", "args": ["mcp"]})

            kimi_toml_fresh = (home / ".kimi-hub/config.toml").read_text()
            self.assertIn('type = "openai"', kimi_toml_fresh)
            self.assertIn("max_context_size = 262144", kimi_toml_fresh)

            opencode = json.loads((home / ".opencode-hub/opencode.json").read_text())
            self.assertEqual(
                set(opencode["provider"]),
                {"cliproxy-claude", "cliproxy-codex", "cliproxy-gemini", "cliproxy-xai", "cliproxy-kimi", "cliproxy-meta"},
            )
            self.assertEqual(opencode["mcp"]["linear"], {"type": "remote", "url": "https://mcp.linear.app/mcp", "enabled": True})
            self.assertEqual(opencode["mcp"]["playwright"], {"type": "local", "command": ["npx", "@playwright/mcp@latest"], "enabled": True})

            gemini = json.loads((home / ".gemini-hub/.gemini/settings.json").read_text())
            self.assertEqual(gemini["security"]["auth"]["selectedType"], "gemini-api-key")
            self.assertEqual(gemini["mcpServers"]["linear"], {"httpUrl": "https://mcp.linear.app/mcp"})
            self.assertEqual(gemini["mcpServers"]["argent"], {"command": "argent", "args": ["mcp"]})

            claude_mcp = json.loads((home / ".claude-hub/.claude.json").read_text())["mcpServers"]
            self.assertIn("linear", claude_mcp)
            self.assertIn("argent", claude_mcp)
            self.assertIn("fly-mcp", claude_mcp)

            codex_toml = (home / ".codex-hub/config.toml").read_text()
            self.assertIn("[mcp_servers.fly-mcp]", codex_toml)
            self.assertIn('command = "argent"', codex_toml)

            grok_toml = (home / ".grok-hub/config.toml").read_text()
            self.assertIn("[mcp_servers.linear]", grok_toml)
            self.assertIn("enabled = true", grok_toml)

            kimi_mcp = json.loads((home / ".kimi-hub/mcp.json").read_text())["mcpServers"]
            self.assertEqual(kimi_mcp["supabase"], {"url": "https://mcp.supabase.com/mcp"})

    def test_helpers_only_update_preserves_existing_configs(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".config/cliproxyapi-mini"
            state = home / ".local/share/cliproxyapi-mini"
            commands = home / ".local/bin"
            config.mkdir(parents=True)
            (config / "client.json").write_text(json.dumps({"base_url": "http://127.0.0.1:8317", "api_key": "k"}))
            codex_cfg = home / ".codex-hub/config.toml"
            codex_cfg.parent.mkdir(parents=True)
            codex_cfg.write_text('custom = true\n')

            with patch.multiple(INSTALLER, HERE=ROOT, USER_DIR=home, CONFIG=config, STATE=state, COMMANDS=commands), \
                    patch.object(sys, "argv", ["install.py", "--helpers"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.main()

            self.assertEqual(codex_cfg.read_text(), 'custom = true\n')
            self.assertTrue((commands / "grok-hub").is_symlink())
            self.assertTrue((commands / "agy-hub").is_symlink())
            self.assertTrue((commands / "opencode-hub").is_symlink())
            self.assertTrue((commands / "muse-hub").is_symlink())

    def test_client_launchers_isolate_homes_and_strip_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            client = {"base_url": "https://mini.tailnet.ts.net:8317/", "api_key": "tok-123"}
            base_env = {
                "ANTHROPIC_API_KEY": "old-anthropic",
                "GOOGLE_API_KEY": "old-google",
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "AGY_ADC_AUTH": "true",
                "GROK_AUTH": "old-grok-auth",
                "GROK_DEPLOYMENT_KEY": "old-deploy",
            }
            with patch.object(HUBCTL, "USER_DIR", home):
                claude_env = HUBCTL.build_client_env("claude", client, base_env)
                self.assertEqual(claude_env["CLAUDE_CONFIG_DIR"], str(home / ".claude-hub"))
                self.assertEqual(claude_env["ANTHROPIC_BASE_URL"], "https://mini.tailnet.ts.net:8317")
                self.assertEqual(claude_env["ANTHROPIC_AUTH_TOKEN"], "tok-123")
                self.assertNotIn("ANTHROPIC_API_KEY", claude_env)

                grok_env = HUBCTL.build_client_env("grok", client, base_env)
                self.assertEqual(grok_env["GROK_HOME"], str(home / ".grok-hub"))
                self.assertEqual(grok_env["XAI_API_KEY"], "tok-123")
                self.assertEqual(grok_env["GROK_XAI_API_BASE_URL"], "https://mini.tailnet.ts.net:8317/v1")
                self.assertEqual(grok_env["GROK_MODELS_BASE_URL"], "https://mini.tailnet.ts.net:8317/v1")
                self.assertNotIn("GROK_AUTH", grok_env)
                self.assertNotIn("GROK_DEPLOYMENT_KEY", grok_env)

                gemini_env = HUBCTL.build_client_env("gemini", client, base_env)
                self.assertEqual(gemini_env["GEMINI_CLI_HOME"], str(home / ".gemini-hub"))
                self.assertEqual(gemini_env["GOOGLE_GEMINI_BASE_URL"], "https://mini.tailnet.ts.net:8317")
                self.assertEqual(gemini_env["GEMINI_API_KEY"], "tok-123")
                self.assertNotIn("GOOGLE_API_KEY", gemini_env)
                self.assertNotIn("GOOGLE_GENAI_USE_VERTEXAI", gemini_env)

                agy_env = HUBCTL.build_client_env("agy", client, base_env)
                self.assertEqual(agy_env["GOOGLE_GEMINI_BASE_URL"], "https://mini.tailnet.ts.net:8317")
                self.assertEqual(agy_env["GEMINI_API_KEY"], "tok-123")
                self.assertNotIn("GOOGLE_API_KEY", agy_env)
                self.assertNotIn("AGY_ADC_AUTH", agy_env)

                kimi_env = HUBCTL.build_client_env("kimi", client, base_env)
                self.assertEqual(kimi_env["KIMI_CODE_HOME"], str(home / ".kimi-hub"))
                self.assertEqual(kimi_env["KIMI_SHARE_DIR"], str(home / ".kimi-hub"))
                self.assertEqual(kimi_env["KIMI_BASE_URL"], "https://mini.tailnet.ts.net:8317/v1")
                self.assertEqual(kimi_env["KIMI_API_KEY"], "tok-123")

                opencode_env = HUBCTL.build_client_env("opencode", client, base_env)
                self.assertEqual(opencode_env["OPENCODE_CONFIG_DIR"], str(home / ".opencode-hub"))
                self.assertEqual(opencode_env["OPENCODE_CONFIG"], str(home / ".opencode-hub/opencode.json"))
                self.assertEqual(opencode_env["CLIPROXY_API_KEY"], "tok-123")

                muse_env = HUBCTL.build_client_env("muse", client, base_env)
                self.assertEqual(muse_env["META_API_KEY"], "tok-123")
                self.assertNotIn("XDG_CONFIG_HOME", muse_env)

                expected_ep = [
                    "-c",
                    'endpoint_transport.base_url="https://mini.tailnet.ts.net:8317/v1"',
                    "-c",
                    'endpoint_transport.auth="bearer"',
                ]
                self.assertEqual(HUBCTL.build_muse_args(client, []), expected_ep)
                self.assertEqual(HUBCTL.build_muse_args(client, ["exec", "--list-models"]), ["exec", *expected_ep, "--list-models"])
                self.assertEqual(HUBCTL.build_muse_args(client, ["serve", "--provider", "meta"]), ["serve", *expected_ep, "--provider", "meta"])
                self.assertEqual(HUBCTL.build_muse_args(client, ["acp"]), ["acp", *expected_ep])
                self.assertEqual(HUBCTL.build_muse_args(client, ["mcp", "list"]), ["mcp", "list"])
                self.assertEqual(HUBCTL.build_muse_args(client, ["--version"]), ["--version"])
                self.assertEqual(HUBCTL.build_muse_args(client, ["hello"]), [*expected_ep, "hello"])

                generic_env = HUBCTL.build_generic_env(client, base_env)
                self.assertEqual(generic_env["OPENAI_BASE_URL"], "https://mini.tailnet.ts.net:8317/v1")
                self.assertEqual(generic_env["META_API_BASE_URL"], "https://mini.tailnet.ts.net:8317/v1")
                self.assertEqual(generic_env["MODEL_API_KEY"], "tok-123")
                self.assertEqual(generic_env["META_API_KEY"], "tok-123")

    def test_sync_clients_populates_all_providers_and_merges_mcps_non_destructively(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".config/cliproxyapi-mini"
            state = home / ".local/share/cliproxyapi-mini"
            config.mkdir(parents=True)
            state.mkdir(parents=True)
            (config / "mcp-servers.json").write_text((ROOT / "mcp-servers.json").read_text())

            # Claude has linear enabled via plugin so sync must skip linear to avoid duplicates.
            claude_settings = home / ".claude-hub/settings.json"
            claude_settings.parent.mkdir(parents=True)
            claude_settings.write_text(json.dumps({"enabledPlugins": {"linear@claude-plugins-official": True}}))

            opencode_path = home / ".opencode-hub/opencode.json"
            opencode_path.parent.mkdir(parents=True)
            opencode_path.write_text(json.dumps({
                "provider": {
                    "my-local-ollama": {"name": "Ollama", "options": {"baseURL": "http://127.0.0.1:11434/v1"}},
                    "cliproxy-stale": {"name": "Old"},
                },
                "mcp": {
                    "custom-mcp": {"type": "remote", "url": "https://custom.example/mcp", "enabled": True},
                },
            }))
            codex_path = home / ".codex-hub/config.toml"
            codex_path.parent.mkdir(parents=True)
            codex_path.write_text(
                'model_provider = "cliproxy"\n\n'
                "[model_providers.cliproxy]\n"
                'name = "Mini hub"\n'
                'base_url = "https://old.tailnet.ts.net:8317/v1"\n'
                'env_key = "CLIPROXY_API_KEY"\n\n'
                "[mcp_servers.linear]\n"
                'url = "https://custom.linear.override/mcp"\n\n'
                "[projects.\"/tmp\"]\n"
                'trust_level = "trusted"\n'
            )
            kimi_path = home / ".kimi-hub/config.toml"
            kimi_path.parent.mkdir(parents=True)
            kimi_path.write_text(
                'default_model = "custom-kimi"\n\n'
                "[providers.custom]\n"
                'type = "kimi"\n'
                'base_url = "https://custom.kimi.example/v1"\n\n'
                "[providers.cliproxy]\n"
                'type = "openai_legacy"\n'
                'base_url = "https://old.tailnet.ts.net:8317/v1"\n'
                'api_key_env = "KIMI_API_KEY"\n'
            )
            # Pre-create an empty 0-byte Antigravity mcp_config.json to ensure empty files merge cleanly.
            agy_mcp_path = home / ".gemini/config/mcp_config.json"
            agy_mcp_path.parent.mkdir(parents=True)
            agy_mcp_path.write_text("")
            # Pre-create Muse settings.json with existing user preferences to ensure extend-only merge.
            muse_cfg_path = home / ".config/muse/settings.json"
            muse_cfg_path.parent.mkdir(parents=True)
            muse_cfg_path.write_text(json.dumps({"schema_version": 1, "provider": "meta", "model": "muse-spark-1.3"}))

            client = {"base_url": "https://mini.tailnet.ts.net:8317", "api_key": "tok-sync"}
            out = io.StringIO()
            with patch.multiple(HUBCTL, USER_DIR=home, CONFIG=config, STATE=state), \
                    patch.object(HUBCTL, "request", return_value=SAMPLE_MODELS), \
                    contextlib.redirect_stdout(out):
                HUBCTL.sync_clients(client)

            opencode = json.loads(opencode_path.read_text())
            self.assertIn("my-local-ollama", opencode["provider"])
            self.assertNotIn("cliproxy-stale", opencode["provider"])
            self.assertIn("claude-opus-5-5", opencode["provider"]["cliproxy-claude"]["models"])
            self.assertIn("claude-sonnet-5-5-high", opencode["provider"]["cliproxy-claude"]["models"])
            self.assertIn("gemini-4-argon-high", opencode["provider"]["cliproxy-gemini"]["models"])
            self.assertNotIn("gemini-3.1-flash-image", opencode["provider"]["cliproxy-gemini"]["models"])
            self.assertIn("gpt-6.1-sol", opencode["provider"]["cliproxy-codex"]["models"])
            self.assertNotIn("gpt-image-2.5", opencode["provider"]["cliproxy-codex"]["models"])
            self.assertIn("grok-4.7", opencode["provider"]["cliproxy-xai"]["models"])
            self.assertNotIn("grok-tts", opencode["provider"]["cliproxy-xai"]["models"])
            self.assertIn("kimi-k3.1-pro", opencode["provider"]["cliproxy-kimi"]["models"])
            self.assertIn("muse-spark-1.3", opencode["provider"]["cliproxy-meta"]["models"])
            self.assertIn("custom-mcp", opencode["mcp"])
            self.assertIn("supabase", opencode["mcp"])
            self.assertNotIn("tok-sync", opencode_path.read_text())

            claude_mcps = json.loads((home / ".claude-hub/.claude.json").read_text())["mcpServers"]
            self.assertNotIn("linear", claude_mcps)
            self.assertIn("argent", claude_mcps)
            self.assertIn("fly-mcp", claude_mcps)

            agy_mcps = json.loads((home / ".gemini/config/mcp_config.json").read_text())["mcpServers"]
            self.assertEqual(agy_mcps["linear"], {"serverUrl": "https://mcp.linear.app/mcp"})
            self.assertEqual(agy_mcps["playwright"], {"command": "npx", "args": ["@playwright/mcp@latest"]})

            codex_toml = codex_path.read_text()
            self.assertIn('base_url = "https://mini.tailnet.ts.net:8317/v1"', codex_toml)
            self.assertIn('url = "https://custom.linear.override/mcp"', codex_toml)
            self.assertEqual(codex_toml.count("[mcp_servers.linear]"), 1)
            self.assertIn("[mcp_servers.supabase]", codex_toml)
            self.assertIn('trust_level = "trusted"', codex_toml)

            kimi_toml = kimi_path.read_text()
            self.assertIn('default_model = "custom-kimi"', kimi_toml)
            self.assertIn("[providers.custom]", kimi_toml)
            self.assertIn('type = "openai"', kimi_toml)
            self.assertNotIn('type = "openai_legacy"', kimi_toml)
            self.assertIn('base_url = "https://mini.tailnet.ts.net:8317/v1"', kimi_toml)
            self.assertIn('[models."kimi-k3.1-pro"]', kimi_toml)
            self.assertIn('[models."gpt-6.1-sol"]', kimi_toml)
            self.assertIn("max_context_size = 262144", kimi_toml)

            muse_cfg = json.loads(muse_cfg_path.read_text())
            self.assertEqual(muse_cfg["provider"], "meta")
            self.assertEqual(muse_cfg["model"], "muse-spark-1.3")
            self.assertEqual(muse_cfg["mcpServers"]["linear"], {"type": "streamable-http", "url": "https://mcp.linear.app/mcp"})

    def test_agy_mode_switches_cleanly_and_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".config/cliproxyapi-mini"
            state = home / ".local/share/cliproxyapi-mini"
            config.mkdir(parents=True)
            state.mkdir(parents=True)
            (config / "mcp-servers.json").write_text((ROOT / "mcp-servers.json").read_text())
            agy_settings = home / ".gemini/antigravity-cli/settings.json"
            agy_settings.parent.mkdir(parents=True)
            agy_settings.write_text(json.dumps({"colorScheme": "tokyo night"}))

            with patch.multiple(HUBCTL, USER_DIR=home, CONFIG=config, STATE=state), \
                    contextlib.redirect_stdout(io.StringIO()):
                HUBCTL.set_agy_mode("hub")
                data = json.loads(agy_settings.read_text())
                self.assertEqual(data["modelProvider"], "gemini")
                self.assertEqual(data["colorScheme"], "tokyo night")
                self.assertTrue((home / ".gemini/config/mcp_config.json").exists())

                HUBCTL.set_agy_mode("account")
                data = json.loads(agy_settings.read_text())
                self.assertNotIn("modelProvider", data)
                self.assertEqual(data["colorScheme"], "tokyo night")

    def test_named_client_export_and_revoke_uses_etag_and_never_prints_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".config/cliproxyapi-mini"
            state = home / ".local/share/cliproxyapi-mini"
            config.mkdir(parents=True)
            state.mkdir(parents=True)
            for filename in ("install.py", "hubctl.py", "mcp-servers.json"):
                (state / filename).write_text((ROOT / filename).read_text())
            secrets_data = {
                "api_key": "hub-primary-key",
                "management_key": "hub-mgmt-key",
                "remote_url": "https://mini.tailnet.ts.net:8317",
            }
            (config / "secrets.json").write_text(json.dumps(secrets_data))

            server_keys = ["hub-primary-key"]
            etag = '"sha256-v1"'
            seen_if_match = []

            def fake_request(path, key, base=HUBCTL.LOCAL_URL, payload=None, method=None, extra_headers=None):
                nonlocal server_keys, etag
                self.assertEqual(path, "/v8/management/config/access/api-keys")
                self.assertEqual(key, "hub-mgmt-key")
                if method == "PUT":
                    seen_if_match.append((extra_headers or {}).get("If-Match"))
                    server_keys = list(payload)
                    etag = '"sha256-v2"'
                    return {"status": "ok"}, {"ETag": etag}
                return list(server_keys), {"ETag": etag}

            export_dir = home / "bundle-mbp"
            out = io.StringIO()
            with patch.multiple(HUBCTL, USER_DIR=home, CONFIG=config, STATE=state), \
                    patch.object(HUBCTL, "request_with_headers", side_effect=fake_request), \
                    patch.object(sys, "argv", ["hubctl", "export-client", str(export_dir), "--name", "macbook-pro"]), \
                    contextlib.redirect_stdout(out):
                HUBCTL.main()

            exported = json.loads((export_dir / "client.json").read_text())
            self.assertEqual(exported["client_name"], "macbook-pro")
            self.assertEqual(exported["base_url"], "https://mini.tailnet.ts.net:8317")
            self.assertNotEqual(exported["api_key"], "hub-primary-key")
            self.assertTrue((export_dir / "mcp-servers.json").exists())
            self.assertIn(exported["api_key"], server_keys)
            self.assertEqual(seen_if_match, ['"sha256-v1"'])
            self.assertNotIn(exported["api_key"], out.getvalue())
            self.assertNotIn("hub-mgmt-key", out.getvalue())

            saved_secrets = json.loads((config / "secrets.json").read_text())
            self.assertEqual(saved_secrets["client_keys"]["macbook-pro"], exported["api_key"])

            revoke_out = io.StringIO()
            with patch.multiple(HUBCTL, USER_DIR=home, CONFIG=config, STATE=state), \
                    patch.object(HUBCTL, "request_with_headers", side_effect=fake_request), \
                    patch.object(sys, "argv", ["hubctl", "revoke-client", "macbook-pro"]), \
                    contextlib.redirect_stdout(revoke_out):
                HUBCTL.main()

            self.assertEqual(server_keys, ["hub-primary-key"])
            self.assertEqual(seen_if_match, ['"sha256-v1"', '"sha256-v2"'])
            self.assertNotIn(exported["api_key"], revoke_out.getvalue())

            # Existing destination must fail before mutating hub API keys.
            existing_dir = home / "already-exists"
            existing_dir.mkdir()
            with patch.multiple(HUBCTL, USER_DIR=home, CONFIG=config, STATE=state), \
                    patch.object(HUBCTL, "request_with_headers", side_effect=fake_request), \
                    patch.object(sys, "argv", ["hubctl", "export-client", str(existing_dir), "--name", "should-not-register"]):
                with self.assertRaises(SystemExit):
                    HUBCTL.main()
            self.assertEqual(server_keys, ["hub-primary-key"])

    def test_login_supports_all_builtin_providers_and_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".config/cliproxyapi-mini"
            state = home / ".local/share/cliproxyapi-mini"
            config.mkdir(parents=True)
            state.mkdir(parents=True)
            (config / "secrets.json").write_text(json.dumps({"api_key": "a", "management_key": "m"}))
            commands = []

            def fake_run(cmd, check=False):
                commands.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with patch.multiple(HUBCTL, USER_DIR=home, CONFIG=config, STATE=state), \
                    patch.object(HUBCTL.os, "chdir"), \
                    patch.object(HUBCTL.subprocess, "run", side_effect=fake_run), \
                    patch.object(HUBCTL, "enable_websockets") as ws_mock:
                for provider, expected_flag in HUBCTL.LOGIN_PROVIDERS.items():
                    with patch.object(sys, "argv", ["hubctl", "login", provider, "--no-browser"]):
                        HUBCTL.main()
                    self.assertIn(expected_flag, commands[-1])
                    self.assertIn("--no-browser", commands[-1])
                self.assertEqual(ws_mock.call_count, 1)

                with patch.object(sys, "argv", ["hubctl", "login", "codex", "--browser", "--oauth-callback-port", "1456"]):
                    HUBCTL.main()
                self.assertIn("--codex-login", commands[-1])
                self.assertIn("1456", commands[-1])
                self.assertEqual(ws_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
