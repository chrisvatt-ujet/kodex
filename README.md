# kodex

Small wrapper for running the Codex CLI with profile-specific `CODEX_HOME`
directories.

Each profile gets its own Codex state directory, including its own `auth.json`,
so multiple people can share the same machine without swapping auth files under
a running Codex process.

## Usage

```sh
kodex <profile> [codex args...]
```

Examples:

```sh
kodex alice
kodex bob exec "summarize this repo"
KODEX_PROFILES_DIR=/Users/Shared/codex-profiles kodex alice
```

By default, profiles live under:

```sh
~/.kodex/profiles/<profile>
```

The wrapper prompts before creating a missing profile.

## Install

Put `kodex` somewhere on your `PATH`, for example:

```sh
mkdir -p ~/.local/bin
cp kodex ~/.local/bin/kodex
chmod +x ~/.local/bin/kodex
```

You can override the Codex binary with `KODEX_CODEX_BIN` if needed.
