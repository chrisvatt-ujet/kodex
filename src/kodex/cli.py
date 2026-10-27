from __future__ import annotations

import datetime as dt
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Never, TextIO, TypedDict, cast

from .version import package_version

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_SHARED_DIRS = (
    "vendor_imports",
    "cache",
    "plugins",
    "ambient-suggestions",
    "node_repl",
    "sqlite",
    "archived_sessions",
    "shell_snapshots",
    "sessions",
    "log",
    "automations",
    ".tmp",
    "skills",
    "computer-use",
    "tmp",
    "memories",
    "app-server-control",
    "app-server-daemon",
)
AUTH_FILE_NAME = "auth.json"

USAGE_TEXT = """Usage: kodex profile <profile> [codex args...]
       kodex profile <profile> usage [--json]
       kodex auto [codex args...]
       kodex usage [--json]
       kodex init
       kodex --version

Runs codex with CODEX_HOME set to a profile-specific directory.

Environment:
  KODEX_BASE_DIR      Shared directory root. Default: ~/.kodex/base
  KODEX_PROFILES_DIR  Profile root directory. Default: ~/.kodex/profiles
  KODEX_SOURCE_DIR    Source Codex home for init. Default: ~/.codex
  KODEX_SHARED_DIRS   Space-separated directory names to symlink from base.
  KODEX_CODEX_BIN     Codex binary to run. Default: codex
  KODEX_USAGE_TIMEOUT Seconds to wait for account usage. Default: 30

Examples:
  kodex profile alice
  kodex auto
  kodex usage
  kodex init
  kodex --version
  kodex profile alice usage
  kodex profile alice usage --json
  kodex profile bob exec "summarize this repo"
  KODEX_PROFILES_DIR=/Users/Shared/codex-profiles kodex profile alice
"""

PROFILE_COMMAND_TEXT = """Usage: kodex profile <profile> [codex args...]
       kodex profile <profile> usage [--json]

Runs codex with CODEX_HOME set to the selected profile directory.
"""

PROFILE_USAGE_TEXT = """Usage: kodex profile <profile> usage [--json]

Reads ChatGPT Codex rate-limit usage for the selected profile.

Options:
  --json  Print the raw JSON payload.
"""

ALL_USAGE_TEXT = """Usage: kodex usage [--json]

Reads ChatGPT Codex rate-limit usage for every profile.

Options:
  --json  Print the raw JSON payload.
"""

AUTO_USAGE_TEXT = """Usage: kodex auto [codex args...]

Checks every profile, selects the one with the most available usage, then runs
Codex with that profile.
"""

INIT_USAGE_TEXT = """Usage: kodex init

Initializes the kodex directory by copying ~/.codex into ~/.kodex/base while
excluding auth.json.
"""


class RpcError(TypedDict, total=False):
    message: str


class JsonRpcMessage(TypedDict, total=False):
    id: int
    method: str
    params: JsonObject
    result: JsonObject
    error: RpcError | JsonObject | str


@dataclass(frozen=True, slots=True)
class BaseSettings:
    base_dir: Path
    profiles_dir: Path
    codex_bin: str
    shared_dirs_override: tuple[str, ...] | None
    usage_timeout: float


@dataclass(frozen=True, slots=True)
class Settings:
    profile: str
    codex_args: tuple[str, ...]
    base: BaseSettings

    @property
    def base_dir(self) -> Path:
        return self.base.base_dir

    @property
    def profiles_dir(self) -> Path:
        return self.base.profiles_dir

    @property
    def codex_bin(self) -> str:
        return self.base.codex_bin

    @property
    def shared_dirs_override(self) -> tuple[str, ...] | None:
        return self.base.shared_dirs_override

    @property
    def usage_timeout(self) -> float:
        return self.base.usage_timeout

    @property
    def profile_dir(self) -> Path:
        return self.profiles_dir / self.profile


@dataclass(frozen=True, slots=True)
class UsageReport:
    profile: str
    account_response: JsonRpcMessage | None
    rate_response: JsonRpcMessage | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class UsageScore:
    available_percent: float
    used_percent: float
    reached: bool


@dataclass(frozen=True, slots=True)
class UsageOptions:
    output_json: bool
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class InitResult:
    copied: int
    skipped: int


class KodexError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def fail(message: str, code: int = 1) -> Never:
    raise KodexError(message, code)


def print_usage(stream: TextIO = sys.stderr) -> None:
    print(USAGE_TEXT, end="", file=stream)


def print_version(stream: TextIO = sys.stdout) -> None:
    print(f"kodex {package_version()}", file=stream)


def print_profile_usage(stream: TextIO = sys.stderr) -> None:
    print(PROFILE_USAGE_TEXT, end="", file=stream)


def print_all_usage(stream: TextIO = sys.stderr) -> None:
    print(ALL_USAGE_TEXT, end="", file=stream)


def print_profile_command(stream: TextIO = sys.stderr) -> None:
    print(PROFILE_COMMAND_TEXT, end="", file=stream)


def print_auto_usage(stream: TextIO = sys.stderr) -> None:
    print(AUTO_USAGE_TEXT, end="", file=stream)


def print_init_usage(stream: TextIO = sys.stderr) -> None:
    print(INIT_USAGE_TEXT, end="", file=stream)


def parse_base_settings(env: Mapping[str, str]) -> BaseSettings:
    return BaseSettings(
        base_dir=env_path(env, "KODEX_BASE_DIR", Path.home() / ".kodex" / "base"),
        profiles_dir=env_path(env, "KODEX_PROFILES_DIR", Path.home() / ".kodex" / "profiles"),
        codex_bin=env.get("KODEX_CODEX_BIN", "codex"),
        shared_dirs_override=parse_shared_dirs_override(env.get("KODEX_SHARED_DIRS")),
        usage_timeout=parse_usage_timeout(env.get("KODEX_USAGE_TIMEOUT", "30")),
    )


def settings_for_profile(
    base: BaseSettings, profile: str, codex_args: Sequence[str] = ()
) -> Settings:
    validate_profile_name(profile)
    return Settings(profile=profile, codex_args=tuple(codex_args), base=base)


def validate_profile_name(profile: str) -> None:
    if profile in {".", ".."} or not PROFILE_RE.fullmatch(profile):
        fail(f"invalid profile '{profile}'; use only letters, numbers, dot, underscore, or hyphen")


def env_path(env: Mapping[str, str], name: str, default: Path) -> Path:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    return Path(raw).expanduser()


def parse_usage_timeout(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        fail("KODEX_USAGE_TIMEOUT must be a number")


def parse_shared_dirs_override(raw: str | None) -> tuple[str, ...] | None:
    if raw is None or raw.strip() == "":
        return None
    return unique_shared_dirs(raw.split())


def unique_shared_dirs(names: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()

    for name in names:
        validate_shared_dir_name(name)
        if name not in seen:
            result.append(name)
            seen.add(name)

    return tuple(result)


def validate_shared_dir_name(name: str) -> None:
    if name in {"", ".", ".."} or "/" in name or "\\" in name:
        fail(f"invalid shared directory name '{name}'")


def command_exists(command: str) -> bool:
    if "/" in command or "\\" in command:
        candidate = Path(command).expanduser()
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(command) is not None


def ensure_codex_bin(base: BaseSettings) -> None:
    if not command_exists(base.codex_bin):
        fail(
            f"could not find codex binary '{base.codex_bin}'; "
            "set KODEX_CODEX_BIN=/path/to/codex"
        )


def confirm(prompt: str, stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr) -> bool:
    if not stdin.isatty():
        return False

    print(f"{prompt} [y/N] ", end="", file=stderr, flush=True)
    answer = stdin.readline().strip()
    return answer in {"y", "Y", "yes", "YES", "Yes"}


def is_empty_dir(path: Path) -> bool:
    try:
        next(path.iterdir())
    except StopIteration:
        return True
    return False


def chmod_private(path: Path) -> None:
    path.chmod(0o700)


def ensure_profile_home(settings: Settings) -> None:
    settings.base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings.profiles_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    chmod_private(settings.base_dir)
    chmod_private(settings.profiles_dir)

    profile_dir = settings.profile_dir
    if not profile_dir.is_dir():
        print(
            f"kodex: profile '{settings.profile}' does not exist at {profile_dir}",
            file=sys.stderr,
        )
        if confirm("Create it now?"):
            profile_dir.mkdir(mode=0o700, parents=True)
            print(f"kodex: created profile '{settings.profile}'", file=sys.stderr)
        else:
            fail("profile not created")

    chmod_private(profile_dir)

    for name in shared_dirs(settings.base_dir, settings.shared_dirs_override):
        ensure_shared_dir_link(profile_dir, settings.base_dir, name, settings.profile)


def shared_dirs(base_dir: Path, override: tuple[str, ...] | None) -> tuple[str, ...]:
    if override is not None:
        return override

    names: list[str] = list(DEFAULT_SHARED_DIRS)
    seen: set[str] = set(names)

    for child in sorted(base_dir.iterdir(), key=lambda path: path.name):
        if child.is_dir() and child.name not in seen:
            validate_shared_dir_name(child.name)
            names.append(child.name)
            seen.add(child.name)

    return tuple(names)


def ensure_shared_dir_link(profile_dir: Path, base_dir: Path, name: str, profile: str) -> None:
    validate_shared_dir_name(name)

    base_path = base_dir / name
    profile_path = profile_dir / name
    base_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    chmod_private(base_path)

    if profile_path.is_symlink():
        target = Path(os.readlink(profile_path))
        if target != base_path:
            fail(f"'{profile_path}' is a symlink to '{target}', expected '{base_path}'")
        return

    if profile_path.exists():
        if not profile_path.is_dir():
            fail(f"'{profile_path}' exists and is not a directory")

        if is_empty_dir(profile_path):
            profile_path.rmdir()
        elif is_empty_dir(base_path):
            move_directory_contents(profile_path, base_path)
            profile_path.rmdir()
            print(
                f"kodex: moved existing '{name}' directory for "
                f"profile '{profile}' into {base_path}",
                file=sys.stderr,
            )
        else:
            fail(f"cannot link '{profile_path}'; both it and '{base_path}' contain files")

    profile_path.symlink_to(base_path, target_is_directory=True)


def move_directory_contents(source: Path, destination: Path) -> None:
    for child in source.iterdir():
        shutil.move(str(child), str(destination / child.name))


def run_init_command(base: BaseSettings, args: Sequence[str], env: Mapping[str, str]) -> int:
    if args:
        if args[0] in {"-h", "--help"}:
            print_init_usage()
            return 2
        fail(f"unknown init option '{args[0]}'")

    source_dir = env_path(env, "KODEX_SOURCE_DIR", Path.home() / ".codex")
    if not source_dir.is_dir():
        fail(f"source Codex directory does not exist: {source_dir}")

    base.base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    base.profiles_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    chmod_private(base.base_dir)
    chmod_private(base.profiles_dir)

    result = copy_codex_home(source_dir, base.base_dir)
    print(
        f"kodex: initialized {base.base_dir} from {source_dir} "
        f"({result.copied} copied, {result.skipped} skipped)",
        file=sys.stderr,
    )
    return 0


def copy_codex_home(source: Path, destination: Path) -> InitResult:
    copied = 0
    skipped = 0

    for child in source.iterdir():
        if should_skip_init_path(child):
            skipped += 1
            continue

        result = copy_init_path(child, destination / child.name)
        copied += result.copied
        skipped += result.skipped

    return InitResult(copied=copied, skipped=skipped)


def copy_init_path(source: Path, destination: Path) -> InitResult:
    if should_skip_init_path(source):
        return InitResult(copied=0, skipped=1)

    if source.is_dir() and not source.is_symlink():
        destination.mkdir(parents=True, exist_ok=True)
        copied = 1
        skipped = 0
        for child in source.iterdir():
            result = copy_init_path(child, destination / child.name)
            copied += result.copied
            skipped += result.skipped
        shutil.copystat(source, destination, follow_symlinks=False)
        return InitResult(copied=copied, skipped=skipped)

    if destination.exists() and destination.is_dir() and not destination.is_symlink():
        fail(f"cannot copy '{source}' over existing directory '{destination}'")

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copy2(source, destination, follow_symlinks=False)
    return InitResult(copied=1, skipped=0)


def should_skip_init_path(path: Path) -> bool:
    return path.name.casefold() == AUTH_FILE_NAME


def discover_profiles(profiles_dir: Path) -> tuple[str, ...]:
    if not profiles_dir.is_dir():
        return ()

    profiles: list[str] = []
    for child in sorted(profiles_dir.iterdir(), key=lambda path: path.name):
        if child.is_dir() and PROFILE_RE.fullmatch(child.name):
            profiles.append(child.name)

    return tuple(profiles)


def run_usage_command(settings: Settings, args: Sequence[str], env: Mapping[str, str]) -> int:
    options = parse_profile_usage_args(args)
    if options.exit_code is not None:
        return options.exit_code

    ensure_codex_bin(settings.base)
    ensure_profile_home(settings)
    responses = read_account_usage(settings, env)
    rate_response = responses[3]
    error_message = response_error_message(rate_response)
    if error_message is not None:
        print(
            f"kodex: usage unavailable for profile '{settings.profile}': {error_message}",
            file=sys.stderr,
        )
        if (
            "authentication required" in error_message
            or "chatgpt authentication required" in error_message
        ):
            print(
                f"kodex: run 'kodex {settings.profile} login' and choose ChatGPT auth",
                file=sys.stderr,
            )
        return 1

    account_response = responses.get(2)
    if options.output_json:
        print_usage_json(settings.profile, account_response, rate_response)
    else:
        print_usage_human(settings.profile, account_response, rate_response)

    return 0


def parse_profile_usage_args(args: Sequence[str]) -> UsageOptions:
    output_json = False

    for arg in args:
        match arg:
            case "--json":
                output_json = True
            case "-h" | "--help":
                print_profile_usage()
                return UsageOptions(output_json=False, exit_code=2)
            case _:
                fail(f"unknown usage option '{arg}'")

    return UsageOptions(output_json=output_json)


def parse_all_usage_args(args: Sequence[str]) -> UsageOptions:
    output_json = False

    for arg in args:
        match arg:
            case "--json":
                output_json = True
            case "-h" | "--help":
                print_all_usage()
                return UsageOptions(output_json=False, exit_code=2)
            case _:
                fail(f"unknown usage option '{arg}'")

    return UsageOptions(output_json=output_json)


def collect_usage_reports(base: BaseSettings, env: Mapping[str, str]) -> list[UsageReport]:
    profiles = discover_profiles(base.profiles_dir)
    if not profiles:
        fail(f"no profiles found under {base.profiles_dir}")

    reports: list[UsageReport] = []
    for profile in profiles:
        settings = settings_for_profile(base, profile)
        try:
            ensure_profile_home(settings)
            responses = read_account_usage(settings, env)
            rate_response = responses[3]
            reports.append(
                UsageReport(
                    profile=profile,
                    account_response=responses.get(2),
                    rate_response=rate_response,
                    error=response_error_message(rate_response),
                )
            )
        except (KodexError, OSError) as error:
            message = error.message if isinstance(error, KodexError) else str(error)
            reports.append(
                UsageReport(
                    profile=profile,
                    account_response=None,
                    rate_response=None,
                    error=message,
                )
            )

    return reports


def run_all_usage_command(base: BaseSettings, args: Sequence[str], env: Mapping[str, str]) -> int:
    options = parse_all_usage_args(args)
    if options.exit_code is not None:
        return options.exit_code
    ensure_codex_bin(base)
    reports = collect_usage_reports(base, env)

    if options.output_json:
        print_all_usage_json(reports)
    else:
        print_all_usage_human(reports)

    return 0 if any(report.error is None for report in reports) else 1


def run_auto_command(base: BaseSettings, codex_args: Sequence[str], env: Mapping[str, str]) -> int:
    if codex_args[:1] in (("-h",), ("--help",)):
        print_auto_usage()
        return 2

    ensure_codex_bin(base)
    reports = collect_usage_reports(base, env)
    selected = select_auto_profile(reports)
    if selected is None:
        fail("no profile has readable, currently available usage")

    report, score = selected
    print(
        f"kodex: auto selected profile '{report.profile}' "
        f"({score.available_percent:.1f}% available)",
        file=sys.stderr,
    )
    settings = settings_for_profile(base, report.profile, codex_args)
    return run_profile_command(settings, env)


def select_auto_profile(reports: Sequence[UsageReport]) -> tuple[UsageReport, UsageScore] | None:
    selected: tuple[UsageReport, UsageScore] | None = None

    for report in reports:
        score = usage_score(report)
        if score is None or score.reached or score.available_percent <= 0:
            continue

        if selected is None or score.available_percent > selected[1].available_percent:
            selected = (report, score)

    return selected


def run_profile_command(settings: Settings, env: Mapping[str, str]) -> int:
    if settings.codex_args[:1] == ("usage",):
        return run_usage_command(settings, settings.codex_args[1:], env)

    ensure_codex_bin(settings.base)
    ensure_profile_home(settings)

    process_env = dict(env)
    process_env["CODEX_HOME"] = str(settings.profile_dir)
    os.execvpe(settings.codex_bin, (settings.codex_bin, *settings.codex_args), process_env)
    fail(f"failed to execute '{settings.codex_bin}'")


def run_profile_entry_command(
    base: BaseSettings, args: Sequence[str], env: Mapping[str, str]
) -> int:
    if not args or args[0] in {"-h", "--help"}:
        print_profile_command()
        return 2

    profile = args[0]
    codex_args = args[1:]
    settings = settings_for_profile(base, profile, codex_args)
    return run_profile_command(settings, env)


def read_account_usage(settings: Settings, env: Mapping[str, str]) -> dict[int, JsonRpcMessage]:
    process_env = dict(env)
    process_env["CODEX_HOME"] = str(settings.profile_dir)

    proc = subprocess.Popen(
        [settings.codex_bin, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=process_env,
    )

    try:
        if proc.stdout is None:
            fail("app-server stdout was not available")
        lines = start_reader(proc.stdout)

        send(
            proc,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "kodex",
                        "title": "kodex",
                        "version": "0.1.0",
                    },
                    "capabilities": {
                        "optOutNotificationMethods": [
                            "remoteControl/status/changed",
                            "account/rateLimits/updated",
                        ],
                    },
                },
            },
        )
        send(proc, {"method": "initialized", "params": {}})
        send(proc, {"id": 2, "method": "account/read", "params": {"refreshToken": False}})
        send(proc, {"id": 3, "method": "account/rateLimits/read"})

        responses = read_responses(lines, {1, 2, 3}, settings.usage_timeout)
    finally:
        shutdown(proc)

    if 1 not in responses:
        fail("timed out waiting for app-server initialization")

    init_error = response_error_message(responses[1])
    if init_error is not None:
        fail(f"app-server initialization failed: {init_error}")

    if 3 not in responses:
        fail("timed out waiting for account usage")

    return responses


def send(proc: subprocess.Popen[str], message: JsonRpcMessage) -> None:
    if proc.stdin is None:
        fail("app-server stdin was not available")
    proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def start_reader(stream: IO[str]) -> queue.Queue[str | None]:
    lines: queue.Queue[str | None] = queue.Queue()

    def read_loop() -> None:
        try:
            for line in stream:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=read_loop, daemon=True).start()
    return lines


def read_responses(
    lines: queue.Queue[str | None], wanted_ids: set[int], timeout: float
) -> dict[int, JsonRpcMessage]:
    deadline = time.monotonic() + timeout
    responses: dict[int, JsonRpcMessage] = {}

    while not wanted_ids.issubset(responses):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            break

        if line is None:
            break

        message = parse_json_rpc_message(line)
        if message is None:
            continue

        message_id = message.get("id")
        if isinstance(message_id, int) and message_id in wanted_ids:
            responses[message_id] = message

    return responses


def parse_json_rpc_message(line: str) -> JsonRpcMessage | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(value, dict):
        return None

    return cast(JsonRpcMessage, value)


def shutdown(proc: subprocess.Popen[str]) -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except BrokenPipeError:
        pass

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def response_error_message(response: JsonRpcMessage) -> str | None:
    error = response.get("error")
    if error is None:
        return None
    if isinstance(error, dict):
        message = error.get("message")
        return str(message if message is not None else error)
    return str(error)


def result_object(response: JsonRpcMessage | None) -> JsonObject | None:
    if response is None:
        return None
    result = response.get("result")
    if isinstance(result, dict):
        return result
    return None


def object_field(source: JsonObject | None, key: str) -> JsonObject | None:
    if source is None:
        return None
    value = source.get(key)
    if isinstance(value, dict):
        return cast(JsonObject, value)
    return None


def text_field(source: JsonObject | None, key: str) -> str | None:
    if source is None:
        return None
    value = source.get(key)
    if isinstance(value, str):
        return value
    return None


def print_usage_json(
    profile: str, account_response: JsonRpcMessage | None, rate_response: JsonRpcMessage
) -> None:
    payload: JsonObject = {
        "profile": profile,
        "account": result_object(account_response),
        "usage": result_object(rate_response) or {},
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def print_usage_human(
    profile: str, account_response: JsonRpcMessage | None, rate_response: JsonRpcMessage
) -> None:
    account_result = result_object(account_response)
    account = object_field(account_result, "account")
    rate_result = result_object(rate_response)
    if rate_result is None:
        fail("account usage response did not include a result object")

    print(f"Profile: {profile}")
    print(f"Account: {account_label(account)}")

    snapshots = usage_snapshots(rate_result)
    for index, snapshot in enumerate(snapshots):
        name = text_field(snapshot, "limitName") or text_field(snapshot, "limitId") or "codex"
        if len(snapshots) == 1:
            print(f"Usage: {name}")
        else:
            if index:
                print()
            print(f"Usage: {name}")

        plan = text_field(snapshot, "planType")
        if plan:
            print(f"  Plan: {plan}")

        print(describe_window("Primary", object_field(snapshot, "primary")))
        secondary = object_field(snapshot, "secondary")
        if secondary is not None:
            print(describe_window("Secondary", secondary))

        credits = describe_credits(object_field(snapshot, "credits"))
        if credits:
            print(credits)

        reached = text_field(snapshot, "rateLimitReachedType")
        if reached:
            print(f"  Limit reached: {reached}")


def print_all_usage_json(reports: Sequence[UsageReport]) -> None:
    payload: JsonObject = {
        "profiles": [usage_report_payload(report) for report in reports],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def usage_report_payload(report: UsageReport) -> JsonObject:
    score = usage_score(report)
    payload: JsonObject = {
        "profile": report.profile,
        "account": result_object(report.account_response),
        "usage": result_object(report.rate_response),
        "error": report.error,
    }
    if score is not None:
        payload["availablePercent"] = score.available_percent
        payload["usedPercent"] = score.used_percent
        payload["rateLimitReached"] = score.reached
    return payload


def print_all_usage_human(reports: Sequence[UsageReport]) -> None:
    for index, report in enumerate(reports):
        if index:
            print()
        print_usage_report_human(report)


def print_usage_report_human(report: UsageReport) -> None:
    if report.error is not None or report.rate_response is None:
        print(f"Profile: {report.profile}")
        print("Usage: unavailable")
        print(f"Error: {report.error or 'unknown error'}")
        return

    print_usage_human(report.profile, report.account_response, report.rate_response)
    score = usage_score(report)
    if score is not None:
        reached = ", rate limit reached" if score.reached else ""
        print(f"Available: {score.available_percent:.1f}%{reached}")


def usage_snapshots(rate_result: JsonObject) -> list[JsonObject]:
    by_id = object_field(rate_result, "rateLimitsByLimitId")
    if by_id:
        return [cast(JsonObject, value) for value in by_id.values() if isinstance(value, dict)]

    rate_limits = object_field(rate_result, "rateLimits")
    return [rate_limits] if rate_limits is not None else []


def usage_score(report: UsageReport) -> UsageScore | None:
    if report.error is not None or report.rate_response is None:
        return None

    rate_result = result_object(report.rate_response)
    if rate_result is None:
        return None

    used_values: list[float] = []
    reached = False

    for snapshot in usage_snapshots(rate_result):
        if text_field(snapshot, "rateLimitReachedType"):
            reached = True

        for window_name in ("primary", "secondary"):
            used = numeric_field(object_field(snapshot, window_name), "usedPercent")
            if used is not None:
                used_values.append(max(0.0, min(100.0, used)))

    if not used_values:
        return None

    used_percent = max(used_values)
    available_percent = 0.0 if reached else max(0.0, 100.0 - used_percent)
    return UsageScore(
        available_percent=available_percent,
        used_percent=used_percent,
        reached=reached,
    )


def numeric_field(source: JsonObject | None, key: str) -> float | None:
    if source is None:
        return None
    value = source.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def account_label(account: JsonObject | None) -> str:
    if account is None:
        return "not logged in"

    account_type = text_field(account, "type") or "unknown"
    if account_type == "chatgpt":
        email = text_field(account, "email") or "unknown email"
        plan = text_field(account, "planType")
        return f"{email} ({plan})" if plan else email
    if account_type == "apiKey":
        return "API key"
    return account_type


def describe_window(label: str, window: JsonObject | None) -> str:
    if window is None:
        return f"  {label}: unavailable"

    used = percent(window.get("usedPercent"))
    duration = window.get("windowDurationMins")
    resets = format_time(window.get("resetsAt"))
    duration_text = f", window {duration} min" if duration is not None else ""
    return f"  {label}: {used} used{duration_text}, resets {resets}"


def describe_credits(credits: JsonObject | None) -> str | None:
    if credits is None:
        return None

    if credits.get("unlimited") is True:
        return "  Credits: unlimited"
    if credits.get("hasCredits") is False:
        return "  Credits: none"

    balance = credits.get("balance")
    if balance:
        return f"  Credits: available, balance {balance}"
    if credits.get("hasCredits"):
        return "  Credits: available"
    return "  Credits: unknown"


def percent(value: JsonValue | None) -> str:
    if value is None or isinstance(value, bool):
        return "unknown"
    if isinstance(value, int):
        return f"{value}%"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value)}%"
        return f"{value:.1f}%"
    return "unknown"


def format_time(value: JsonValue | None) -> str:
    if value is None or isinstance(value, bool) or not isinstance(value, int | float):
        return "unknown"
    try:
        timestamp = dt.datetime.fromtimestamp(value).astimezone()
    except (OSError, OverflowError, TypeError, ValueError):
        return str(value)
    return timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")


def run(argv: Sequence[str], env: Mapping[str, str]) -> int:
    if argv and argv[0] in {"--version", "-V"}:
        print_version()
        return 0

    if not argv or argv[0] in {"-h", "--help"}:
        print_usage()
        return 2

    base = parse_base_settings(env)
    command = argv[0]
    args = tuple(argv[1:])

    match command:
        case "init":
            return run_init_command(base, args, env)
        case "profile":
            return run_profile_entry_command(base, args, env)
        case "usage":
            return run_all_usage_command(base, args, env)
        case "auto":
            return run_auto_command(base, args, env)
        case _:
            fail(f"unknown command '{command}'")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(tuple(sys.argv[1:] if argv is None else argv), os.environ)
    except KodexError as error:
        print(f"kodex: {error.message}", file=sys.stderr)
        return error.code
    except OSError as error:
        print(f"kodex: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("kodex: interrupted", file=sys.stderr)
        return 130
