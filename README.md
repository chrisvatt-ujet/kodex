# kodex

Small wrapper for running the Codex CLI with profile-specific `CODEX_HOME`
directories.

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
`kodex` as a uv tool, and updates your shell PATH for uv tools. It does not
install uv or Python for you.

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
```

The top-level `./kodex` entrypoint also works when Python 3.14 is available.
You can override the Codex binary with `KODEX_CODEX_BIN` if needed.

Type and lint checks are configured for Python 3.14:

```sh
uv run pyright
uv run ruff check
```
