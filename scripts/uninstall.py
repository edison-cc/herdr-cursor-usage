#!/usr/bin/env python3
"""Remove the Cursor usage mod and the layout clone it created."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_lib


def main(argv=None):
    parser = argparse.ArgumentParser(description="Uninstall the Cursor usage Herdr mod.")
    parser.add_argument("--app-home", help="Herdr app home (default: $HERDR_APP_HOME or ~/.config/herdr-app)")
    args = parser.parse_args(argv)
    try:
        install_lib.uninstall(install_lib.app_home(args.app_home))
    except Exception as exc:  # noqa: BLE001 — the uninstaller reports one actionable line
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
