from __future__ import annotations

from importlib import metadata

PACKAGE_NAME = "kodex"
VERSION_FALLBACK = "0.1.0"


def package_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return VERSION_FALLBACK


__version__ = package_version()
