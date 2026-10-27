from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .cli import KodexError, command_exists, confirm, env_path, fail, validate_profile_name
from .version import package_version

# These can select credentials or a provider outside the chosen Claude home.
AUTH_OVERRIDE_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_UNIX_SOCKET",
    "ANTHROPIC_PROFILE",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "CLAUDE_CODE_HOST_CREDS_FILE",
    "CLAUDE_CODE_HOST_AUTH_ENV_VAR",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
    "CLAUDE_CODE_GATEWAY_TOKEN_FILE_DESCRIPTOR",
    "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR",
    "CCR_AGENT_PROXY_TOKEN_FILE_DESCRIPTOR",
)
PROVIDER_OVERRIDE_VARS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_GATEWAY",
)

USAGE_TEXT = """Usage: {program} profile <profile> [claude args...]
       {program} --version

Runs Claude Code with a private CLAUDE_CONFIG_DIR for the selected profile.
clod and klodex are equivalent; kodex continues to run Codex.

Environment:
  KODEX_CLAUDE_PROFILES_DIR  Profile root. Default: ~/.kodex/claude/profiles
  KODEX_CLAUDE_BIN           Claude binary to run. Default: claude

Examples:
  {program} profile alice auth login --claudeai --email alice@example.com
  {program} profile alice auth status
  {program} profile alice --model sonnet --effort high
  {program} profile bob --model opus --effort medium -p "summarize this repo"

Model, effort, and other arguments are passed unchanged to Claude Code.
Claude auto-selection and usage queries are not supported; select a profile.
"""


def validate_auth_environment(env: Mapping[str, str]) -> None:
    conflicts = [name for name in AUTH_OVERRIDE_VARS if env.get(name)]
    conflicts.extend(
        name
        for name in PROVIDER_OVERRIDE_VARS
        if env.get(name, "").strip().lower() not in {"", "0", "false", "off", "no"}
    )
    if conflicts:
        fail(
            "unset auth/provider overrides before selecting a Claude account: "
            + ", ".join(conflicts)
        )


def ensure_profile_home(profiles_dir: Path, profile: str) -> Path:
    profile_dir = profiles_dir / profile
    if profile_dir.is_symlink() or (profile_dir.exists() and not profile_dir.is_dir()):
        fail(f"Claude profile must be a real directory: '{profile_dir}'")

    if not profile_dir.exists():
        print(f"Claude profile '{profile}' does not exist at {profile_dir}", file=sys.stderr)
        if not confirm("Create it now?"):
            fail("profile not created")
        profiles_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        profile_dir.mkdir(mode=0o700)

    profile_dir.chmod(0o700)
    return profile_dir


def run(argv: Sequence[str], env: Mapping[str, str], program: str = "clod") -> int:
    if argv and argv[0] in {"--version", "-V"}:
        print(f"{program} {package_version()}")
        return 0

    if not argv or argv[0] in {"-h", "--help"}:
        print(USAGE_TEXT.format(program=program), end="", file=sys.stderr)
        return 2

    if argv[0] != "profile":
        fail(f"unknown command '{argv[0]}'; use '{program} profile <profile> [claude args...]'")
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        print(USAGE_TEXT.format(program=program), end="", file=sys.stderr)
        return 2

    profile = argv[1]
    validate_profile_name(profile)
    validate_auth_environment(env)
    claude_bin = env.get("KODEX_CLAUDE_BIN", "claude")
    if not command_exists(claude_bin):
        fail(f"could not find claude binary '{claude_bin}'; set KODEX_CLAUDE_BIN=/path/to/claude")

    # Stable, absolute paths also keep macOS Keychain namespaces consistent.
    profiles_dir = env_path(
        env, "KODEX_CLAUDE_PROFILES_DIR", Path.home() / ".kodex" / "claude" / "profiles"
    ).resolve()
    profile_dir = ensure_profile_home(profiles_dir, profile)
    process_env = dict(env)
    process_env["CLAUDE_CONFIG_DIR"] = str(profile_dir)
    # Claude's host-only override can otherwise point Keychain at another home.
    process_env.pop("CLAUDE_SECURESTORAGE_CONFIG_DIR", None)
    os.execvpe(claude_bin, (claude_bin, *argv[2:]), process_env)
    fail(f"failed to execute '{claude_bin}'")


def main(argv: Sequence[str] | None = None) -> int:
    program = "klodex" if Path(sys.argv[0]).name == "klodex" else "clod"
    try:
        return run(tuple(sys.argv[1:] if argv is None else argv), os.environ, program)
    except KodexError as error:
        print(f"{program}: {error.message}", file=sys.stderr)
        return error.code
    except OSError as error:
        print(f"{program}: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"{program}: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
