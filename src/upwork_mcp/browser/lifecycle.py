"""CLI-safe lifecycle controls for the dedicated Upwork Chrome profile."""

import argparse
import sys

from .client import chrome_debug_status, start_chrome_with_debug, stop_dedicated_chrome


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the dedicated Upwork Chrome process")
    parser.add_argument("action", choices=("ensure", "stop", "status"))
    args = parser.parse_args()

    if args.action == "ensure":
        if not start_chrome_with_debug():
            print(
                "Refusing to attach: port 9222 is unavailable or is not owned by the dedicated Upwork profile.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return
    if args.action == "stop":
        if not stop_dedicated_chrome():
            print("Dedicated Upwork Chrome could not be stopped safely.", file=sys.stderr)
            raise SystemExit(1)
        return

    status = chrome_debug_status()
    print(status)
    raise SystemExit(0 if status == "dedicated" else 1)


if __name__ == "__main__":
    main()
