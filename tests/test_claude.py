from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from kodex import claude, cli


class Executed(Exception):
    """Stop just before replacing the test process with the native CLI."""


class ClaudeProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.profiles = self.root / "profiles"
        self.env = {
            "KODEX_CLAUDE_PROFILES_DIR": str(self.profiles),
            "KODEX_CLAUDE_BIN": sys.executable,
            "PATH": os.defpath,
            "UNRELATED_SETTING": "preserve me",
        }

    def profile(self, name: str = "alice") -> Path:
        directory = self.profiles / name
        directory.mkdir(parents=True)
        return directory

    def launch(
        self, args: list[str], env: dict[str, str] | None = None
    ) -> tuple[str, tuple[str, ...], dict[str, str]]:
        with patch.object(claude.os, "execvpe", side_effect=Executed) as execute:
            with self.assertRaises(Executed):
                claude.run(args, self.env if env is None else env)
        execute.assert_called_once()
        binary, argv, process_env = execute.call_args.args
        return binary, tuple(argv), process_env

    def test_separate_accounts_have_private_unshared_homes(self) -> None:
        alice = self.profile("alice")
        bob = self.profile("bob")
        (alice / "account-state").write_text("alice state")
        _, _, alice_env = self.launch(["profile", "alice"])
        _, _, bob_env = self.launch(["profile", "bob"])
        self.assertEqual(alice_env["CLAUDE_CONFIG_DIR"], str(alice))
        self.assertEqual(bob_env["CLAUDE_CONFIG_DIR"], str(bob))
        self.assertNotEqual(alice_env["CLAUDE_CONFIG_DIR"], bob_env["CLAUDE_CONFIG_DIR"])
        self.assertFalse((bob / "account-state").exists())
        self.assertEqual(list(bob.iterdir()), [])
        for directory in (alice, bob):
            self.assertFalse(directory.is_symlink())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_existing_shared_root_permissions_are_preserved(self) -> None:
        profile = self.profile()
        self.profiles.chmod(0o770)
        profile.chmod(0o775)
        self.launch(["profile", "alice"])
        self.assertEqual(stat.S_IMODE(self.profiles.stat().st_mode), 0o770)
        self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)

    def test_native_model_effort_prompt_and_options_pass_through_exactly(self) -> None:
        self.profile()
        native_args = [
            "--model",
            "opus",
            "--effort",
            "high",
            "--print",
            "Explain this path with spaces; $(do not execute)",
            "--output-format",
            "json",
        ]
        binary, argv, process_env = self.launch(["profile", "alice", *native_args])
        self.assertEqual(binary, sys.executable)
        self.assertEqual(argv, (sys.executable, *native_args))
        self.assertEqual(process_env["UNRELATED_SETTING"], "preserve me")

    def test_login_and_status_pass_through(self) -> None:
        self.profile()
        for native_args in (
            ["auth", "login", "--claudeai", "--email", "alice@example.com"],
            ["auth", "status"],
            ["auth", "logout"],
            ["--help"],
        ):
            with self.subTest(args=native_args):
                _, argv, _ = self.launch(["profile", "alice", *native_args])
                self.assertEqual(argv[1:], tuple(native_args))

    def test_existing_environment_is_not_mutated(self) -> None:
        self.profile()
        self.env.update(
            {
                "CLAUDE_CONFIG_DIR": "/some/other/account",
                "CLAUDE_SECURESTORAGE_CONFIG_DIR": "/some/other/keychain",
                "CLAUDE_CODE_EFFORT_LEVEL": "medium",
            }
        )
        original = dict(self.env)
        _, _, process_env = self.launch(["profile", "alice", "--effort", "high"])
        self.assertEqual(self.env, original)
        self.assertEqual(process_env["CLAUDE_CONFIG_DIR"], str(self.profiles / "alice"))
        self.assertNotIn("CLAUDE_SECURESTORAGE_CONFIG_DIR", process_env)
        self.assertEqual(process_env["CLAUDE_CODE_EFFORT_LEVEL"], "medium")

    def test_empty_secure_storage_override_is_removed(self) -> None:
        self.profile()
        self.env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = ""
        _, _, process_env = self.launch(["profile", "alice"])
        self.assertNotIn("CLAUDE_SECURESTORAGE_CONFIG_DIR", process_env)

    def test_relative_profile_root_becomes_absolute(self) -> None:
        self.profile()
        self.env["KODEX_CLAUDE_PROFILES_DIR"] = "profiles"
        with contextlib.chdir(self.root):
            _, _, process_env = self.launch(["profile", "alice"])
        self.assertEqual(process_env["CLAUDE_CONFIG_DIR"], str(self.profiles / "alice"))

    def test_default_profile_location_does_not_copy_native_claude_state(self) -> None:
        native_home = self.root / ".claude"
        native_home.mkdir()
        (native_home / "settings.json").write_text('{"private": "native account"}')
        default_profile = self.root / ".kodex" / "claude" / "profiles" / "alice"
        default_profile.mkdir(parents=True)
        env = {key: value for key, value in self.env.items() if key != "KODEX_CLAUDE_PROFILES_DIR"}
        with patch.object(claude.Path, "home", return_value=self.root):
            _, _, process_env = self.launch(["profile", "alice"], env)
        self.assertEqual(process_env["CLAUDE_CONFIG_DIR"], str(default_profile))
        self.assertEqual(list(default_profile.iterdir()), [])
        self.assertEqual(
            (native_home / "settings.json").read_text(), '{"private": "native account"}'
        )

    def test_missing_profile_requires_confirmation(self) -> None:
        with patch.object(claude, "confirm", return_value=True) as confirm:
            self.launch(["profile", "alice", "auth", "login", "--claudeai"])
        confirm.assert_called_once()
        self.assertTrue((self.profiles / "alice").is_dir())
        self.assertEqual(stat.S_IMODE(self.profiles.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.profiles / "alice").stat().st_mode), 0o700)

    def test_declined_profile_is_not_created_or_launched(self) -> None:
        with patch.object(claude, "confirm", return_value=False) as confirm:
            with patch.object(claude.os, "execvpe") as execute:
                with self.assertRaises(cli.KodexError):
                    claude.run(["profile", "alice"], self.env)
        confirm.assert_called_once()
        execute.assert_not_called()
        self.assertFalse((self.profiles / "alice").exists())

    def test_traversal_and_invalid_profile_names_are_rejected(self) -> None:
        for name in (".", "..", "../alice", "alice/bob", "alice\\bob", "", "a b"):
            with self.subTest(profile=name):
                with patch.object(claude.os, "execvpe") as execute:
                    with self.assertRaises(cli.KodexError):
                        claude.run(["profile", name], self.env)
                    execute.assert_not_called()
                self.assertFalse(self.profiles.exists())

    def test_symlink_profile_cannot_alias_another_account(self) -> None:
        bob = self.profile("bob")
        (bob / "state").write_text("untouched")
        (self.profiles / "alice").symlink_to(bob, target_is_directory=True)
        with patch.object(claude.os, "execvpe") as execute:
            with self.assertRaises(cli.KodexError):
                claude.run(["profile", "alice"], self.env)
            execute.assert_not_called()
        self.assertEqual((bob / "state").read_text(), "untouched")
        self.assertTrue((self.profiles / "alice").is_symlink())

    def test_file_at_profile_path_is_not_overwritten(self) -> None:
        self.profiles.mkdir()
        profile = self.profiles / "alice"
        profile.write_text("untouched")
        with patch.object(claude.os, "execvpe") as execute:
            with self.assertRaises((cli.KodexError, OSError)):
                claude.run(["profile", "alice"], self.env)
            execute.assert_not_called()
        self.assertEqual(profile.read_text(), "untouched")

    def test_auth_overrides_fail_before_creating_profile_and_redact_values(self) -> None:
        for variable in claude.AUTH_OVERRIDE_VARS:
            with self.subTest(variable=variable):
                env = {**self.env, variable: "never-print-this-secret"}
                with patch.object(claude.os, "execvpe") as execute:
                    with self.assertRaises(cli.KodexError) as caught:
                        claude.run(["profile", "alice"], env)
                    execute.assert_not_called()
                self.assertIn(variable, str(caught.exception))
                self.assertNotIn("never-print-this-secret", str(caught.exception))
                self.assertFalse(self.profiles.exists())

    def test_zero_auth_file_descriptor_is_still_an_override(self) -> None:
        env = {**self.env, "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR": "0"}
        with self.assertRaises(cli.KodexError):
            claude.run(["profile", "alice"], env)
        self.assertFalse(self.profiles.exists())

    def test_provider_overrides_fail_before_creating_profile(self) -> None:
        for variable in claude.PROVIDER_OVERRIDE_VARS:
            with self.subTest(variable=variable):
                with self.assertRaises(cli.KodexError) as caught:
                    claude.run(["profile", "alice"], {**self.env, variable: "1"})
                self.assertIn(variable, str(caught.exception))
                self.assertFalse(self.profiles.exists())

    def test_disabled_provider_flags_and_empty_credentials_are_allowed(self) -> None:
        self.profile()
        for value in ("", "0", "false", "off", "no", "FALSE"):
            with self.subTest(value=value):
                env = {**self.env, "ANTHROPIC_API_KEY": ""}
                env.update(dict.fromkeys(claude.PROVIDER_OVERRIDE_VARS, value))
                self.launch(["profile", "alice"], env)

    def test_unknown_commands_fail_without_creating_profiles(self) -> None:
        for command in ("auto", "usage", "init", "bogus"):
            with self.subTest(command=command):
                with self.assertRaises(cli.KodexError):
                    claude.run([command], self.env)
                self.assertFalse(self.profiles.exists())

    def test_missing_binary_fails_before_creating_profile(self) -> None:
        env = {**self.env, "KODEX_CLAUDE_BIN": str(self.root / "missing-claude")}
        with self.assertRaises(cli.KodexError):
            claude.run(["profile", "alice"], env)
        self.assertFalse(self.profiles.exists())

    def test_both_aliases_are_installed_to_same_entrypoint(self) -> None:
        project = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((project / "pyproject.toml").read_text())
        scripts = metadata["project"]["scripts"]
        self.assertEqual(scripts["clod"], "kodex.claude:main")
        self.assertEqual(scripts["klodex"], scripts["clod"])
        self.assertEqual(scripts["kodex"], "kodex.cli:main")

    def test_help_names_requested_alias_without_touching_accounts(self) -> None:
        for alias in ("clod", "klodex"):
            with self.subTest(alias=alias):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    result = claude.run(["--help"], self.env, program=alias)
                self.assertIn(alias, output.getvalue())
                self.assertIn("profile", output.getvalue())
                self.assertIn(result, (0, 2))
                self.assertFalse(self.profiles.exists())

    def test_native_exit_code_and_process_boundary(self) -> None:
        profile = self.profile()
        native = self.root / "fake claude"
        native.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "print(json.dumps({'argv': sys.argv[1:], "
            "'config': os.environ['CLAUDE_CONFIG_DIR'], "
            "'secure': os.environ.get('CLAUDE_SECURESTORAGE_CONFIG_DIR'), "
            "'unrelated': os.environ.get('UNRELATED_SETTING')}))\n"
            "raise SystemExit(23)\n"
        )
        native.chmod(0o700)
        project = Path(__file__).resolve().parents[1]
        env = {
            **self.env,
            "KODEX_CLAUDE_BIN": str(native),
            "PYTHONPATH": str(project / "src"),
            "CLAUDE_CONFIG_DIR": "/wrong/account",
            "CLAUDE_SECURESTORAGE_CONFIG_DIR": "",
        }
        native_args = ["--model", "opus", "--effort", "high", "-p", "hello with spaces"]
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from kodex.claude import main; raise SystemExit(main())",
                "profile",
                "alice",
                *native_args,
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 23, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["argv"], native_args)
        self.assertEqual(payload["config"], str(profile))
        self.assertIsNone(payload["secure"])
        self.assertEqual(payload["unrelated"], "preserve me")

    def test_existing_codex_profile_keeps_native_model_and_effort_args(self) -> None:
        args = [
            "profile",
            "alice",
            "--model",
            "gpt-example",
            "-c",
            'model_reasoning_effort="high"',
            "exec",
            "a prompt with spaces",
        ]
        env = {
            "KODEX_PROFILES_DIR": str(self.root / "codex-profiles"),
            "KODEX_BASE_DIR": str(self.root / "codex-base"),
            "KODEX_CODEX_BIN": sys.executable,
        }
        with patch.object(cli, "ensure_profile_home"):
            with patch.object(cli.os, "execvpe", side_effect=Executed) as execute:
                with self.assertRaises(Executed):
                    cli.run(args, env)
        binary, argv, process_env = execute.call_args.args
        self.assertEqual(binary, sys.executable)
        self.assertEqual(tuple(argv), (sys.executable, *args[2:]))
        self.assertEqual(process_env["CODEX_HOME"], str(self.root / "codex-profiles" / "alice"))
        self.assertNotIn("CLAUDE_CONFIG_DIR", process_env)


if __name__ == "__main__":
    unittest.main()
