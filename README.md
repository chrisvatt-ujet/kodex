# kodex

Small wrappers for running Codex and Claude Code with separate account profiles:

- `kodex` runs Codex with a profile-specific `CODEX_HOME`.
- `clod` and `klodex` are equivalent names for the Claude Code wrapper.

`kodex` is a typed Python 3.14+ uv project.

Each profile lives under `~/.kodex/profiles`. Shared Codex directories are
symlinked from `~/.kodex/base`, while root files such as `auth.json` stay
profile-specific.

## Usage

```sh
kodex profile <profile> [codex args...]
kodex profile <profile> usage [--json]
kodex auto [codex args...]
kodex usage [--json]
kodex init
kodex --version
```

Examples:

```sh
kodex profile alice
kodex auto
kodex auto exec "summarize this repo"
kodex usage
kodex init
kodex --version
kodex profile alice usage
kodex profile alice usage --json
kodex profile bob exec "summarize this repo"
kodex profile alice --model gpt-6.1-sol -c model_reasoning_effort=high
KODEX_PROFILES_DIR=/Users/Shared/codex-profiles kodex profile alice
```

By default, profiles live under:

```sh
~/.kodex/profiles/<profile>
```

The shared directory root lives under:

```sh
~/.kodex/base
```

`kodex init` imports from `~/.codex` by default. Override that source with
`KODEX_SOURCE_DIR`.

Each profile gets a normal `CODEX_HOME` directory:

```sh
~/.kodex/profiles/<profile>
```

Within each profile, known Codex directories are symlinked to matching
directories in `~/.kodex/base`. Files directly under the profile directory are
not shared, so each profile keeps its own auth and root-level state.

The wrapper prompts before creating a missing profile. If a profile already has
one of the shared directories locally, the wrapper replaces it with a symlink
when it can do so without merging two non-empty directories.

Override the shared directory list with `KODEX_SHARED_DIRS`:

```sh
KODEX_SHARED_DIRS="plugins cache skills log" kodex profile alice
```

## Claude Code

Install Claude Code separately, then sign in once for each profile:

```sh
clod profile alice auth login --claudeai --email alice@example.com
clod profile bob auth login --claudeai --email bob@example.com
clod profile alice auth status
clod profile bob auth status
```

The wrapper prompts before creating a missing profile in an interactive terminal.
The email option prefills the login screen; each person still completes their own
sign-in. Use `auth status` to check which account is signed in.
Use the `--claudeai` subscription login shown above: Claude Console sign-ins
without an API key do not support config-directory account isolation.

Both command names use the same profiles and pass Claude arguments through
unchanged, including model, effort, and noninteractive output options:

```sh
clod profile <profile> [claude args...]
klodex profile <profile> [claude args...]

clod profile alice --model sonnet --effort high
klodex profile bob --model opus --effort medium -p "summarize this repo"
clod profile alice --help
```

Claude validates available models and effort levels. An inherited
`CLAUDE_CODE_EFFORT_LEVEL` takes precedence over `--effort`; unset it to choose
effort with the command-line flag.

Each profile has its own `CLAUDE_CONFIG_DIR`:

```sh
~/.kodex/claude/profiles/<profile>
```

These directories use private `0700` permissions. Claude profiles do not share
directories or copy existing credentials from `~/.claude`. On macOS, Claude uses
separate Keychain entries for each config directory, so keep profile paths
stable after signing in. The wrapper replaces inherited `CLAUDE_CONFIG_DIR` and
removes `CLAUDE_SECURESTORAGE_CONFIG_DIR` so credential storage follows the
selected profile.

Override the profile root or Claude executable when needed:

```sh
KODEX_CLAUDE_PROFILES_DIR=/Users/Shared/claude-profiles clod profile alice
KODEX_CLAUDE_BIN=/path/to/claude clod profile alice
```

Conflicting inherited authentication or provider settings, such as
`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, or an enabled
`CLAUDE_CODE_USE_BEDROCK`, stop the wrapper with the variable names to unset.
Their values are never printed. Other environment variables and Claude's native
settings still apply, including settings that configure alternate authentication.
Profile separation is a convenience for account switching, not a security
boundary between people sharing an operating-system account.

`clod` and `klodex` support explicit profile selection; they do not implement
Codex's quota-based `auto` or `usage` commands. Existing `kodex` commands keep
their behavior. See Claude's [settings](https://code.claude.com/docs/en/settings)
and [authentication](https://code.claude.com/docs/en/authentication) documentation
for native configuration and login details.

## Init

`kodex init` initializes the kodex directory by copying your current Codex home
from `~/.codex` into `~/.kodex/base`, excluding `auth.json` so login credentials
are not copied into shared state:

```sh
kodex init
```

Use `KODEX_SOURCE_DIR` to import from a different Codex home:

```sh
KODEX_SOURCE_DIR=/Users/Shared/codex-template kodex init
```

## Usage Checks

`kodex usage` asks Codex's app-server account API for every profile's ChatGPT
Codex rate-limit usage:

```sh
kodex usage
kodex usage --json
```

`kodex profile <profile> usage` asks Codex's app-server account API for the
selected profile's ChatGPT Codex rate-limit usage.

This requires the profile to be logged in with ChatGPT auth:

```sh
kodex profile alice login
kodex profile alice usage
```

Use `--json` when another script should consume the raw account and rate-limit
payload:

```sh
kodex profile alice usage --json
```

## Auto Mode

`kodex auto [codex args...]` checks every profile, chooses the profile with the
most currently available usage, then runs Codex with that profile:

```sh
kodex auto
kodex auto exec "summarize this repo"
```

Profiles with unreadable usage or a reached rate limit are skipped.

## Install

Install from the checkout:

```sh
./install.sh
```

The installer checks that `uv` and Python 3.14 are already installed, installs
the `kodex`, `clod`, and `klodex` commands as a uv tool, and updates your shell
PATH for uv tools. It does not install uv, Python, Codex, or Claude Code for you.

If a prerequisite is missing, install it first. For example:

```sh
uv python install 3.14
```

If you already have uv and want to do the install manually:

```sh
uv tool install --python 3.14 --no-python-downloads .
uv tool update-shell
```

To skip shell profile updates:

```sh
./install.sh --no-shell
```

During development, run it from the checkout:

```sh
uv run kodex profile alice
uv run clod profile alice
uv run klodex profile bob
```

The top-level `./kodex` entrypoint also works when Python 3.14 is available.
You can override the Codex binary with `KODEX_CODEX_BIN` if needed.

Type and lint checks are configured for Python 3.14:

```sh
uv run pyright
uv run ruff check
uv run python -m unittest discover -s tests
```
