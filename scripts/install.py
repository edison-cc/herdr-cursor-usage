#!/usr/bin/env python3
"""Install the Cursor usage mod into the running Herdr."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_lib


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the Cursor usage Herdr mod.")
    parser.add_argument("--app-home", help="Herdr app home (default: $HERDR_APP_HOME or ~/.config/herdr-app)")
    parser.add_argument("--force", action="store_true", help="Replace owned files that were edited locally")
    args = parser.parse_args(argv)
    try:
        install_lib.install(install_lib.app_home(args.app_home), force=args.force)
    except Exception as exc:  # noqa: BLE001 — the installer reports one actionable line
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
