#!/usr/bin/env sh
set -eu

usage() {
  cat >&2 <<'USAGE'
Usage: ./install.sh [--no-shell]

Installs kodex as a uv tool and makes `kodex`, `clod`, `klod`, `klodex`, and `claus`
available on PATH.

Prerequisites:
  uv
  Python 3.14 visible to uv

Options:
  --no-shell  Skip `uv tool update-shell`.
  -h, --help  Show this help.
USAGE
}

die() {
  printf 'install.sh: %s\n' "$*" >&2
  exit 1
}

find_uv() {
  if [ -n "${UV_BIN:-}" ]; then
    if [ ! -x "$UV_BIN" ]; then
      die "UV_BIN is set to '$UV_BIN', but it is not executable"
    fi
    printf '%s\n' "$UV_BIN"
    return 0
  fi

  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi

  if [ -x "$HOME/.local/bin/uv" ]; then
    printf '%s\n' "$HOME/.local/bin/uv"
    return 0
  fi

  return 1
}

install_shell=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-shell)
      install_shell=0
      shift
      ;;
    -h|--help)
      usage
      exit 2
      ;;
    *)
      die "unknown option '$1'"
      ;;
  esac
done

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

if ! uv_bin=$(find_uv); then
  die "uv is required; install uv first, then rerun this installer"
fi

printf 'Using uv: %s\n' "$uv_bin" >&2

if ! python_bin=$("$uv_bin" python find 3.14 --no-python-downloads 2>/dev/null); then
  die "Python 3.14 is required; install it first, then rerun this installer"
fi

printf 'Using Python: %s\n' "$python_bin" >&2

printf 'Installing kodex as a uv tool...\n' >&2
"$uv_bin" tool install --force --refresh --reinstall --no-python-downloads --python 3.14 "$project_dir"

if [ "$install_shell" -eq 1 ]; then
  printf 'Updating shell PATH for uv tools...\n' >&2
  if ! "$uv_bin" tool update-shell; then
    printf 'install.sh: warning: could not update shell PATH automatically\n' >&2
    printf 'install.sh: add this to your shell profile instead:\n' >&2
    printf '  . "$HOME/.local/bin/env"\n' >&2
  fi
fi

printf '\nInstalled kodex (Codex), clod, klod, klodex, and claus (Claude Code).\n' >&2
printf 'Open a new shell, or run: . "$HOME/.local/bin/env"\n' >&2
printf 'Then try: kodex --help or clod --help\n' >&2
