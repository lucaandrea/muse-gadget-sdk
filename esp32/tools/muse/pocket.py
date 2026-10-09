#!/usr/bin/env python3
"""Provision a revocable companion credential over USB, without printing it."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from chat import Board, BoardError, pick_port


def response(board, prefix, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = board.read_line(deadline)
        if line and line.startswith(prefix):
            return line[len(prefix):]
    raise BoardError("No pocket response. Install the pocket firmware first.")


def provision(board, data):
    url, token, ca = data["url"], data["token"], data.get("ca", "")
    parsed = urlparse(url)
    if parsed.scheme != "wss" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or len(url) > 256:
        raise BoardError("Use a wss URL without credentials, query or fragment")
    if not 16 <= len(token) < 129 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise BoardError("Invalid device credential")
    if len(ca.encode()) > 4096:
        raise BoardError("CA certificate must fit 4096 bytes")
    access = data.get("external_access_token", "")
    if len(access) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in access):
        raise BoardError("Invalid private-app access credential")
    # Set URL last: partially written setup will not enable the companion.
    commands = ["pocket.url=", "pocket.token=" + token, "pocket.ca="]
    commands += ["pocket.ca+=" + ca[i:i+300].replace("\n", "\\n") for i in range(0, len(ca), 300)]
    commands.append("pocket.access=")
    commands += ["pocket.access+=" + access[i:i+300] for i in range(0, len(access), 300)]
    commands.append("pocket.url=" + url)
    for command in commands:
        board.write_line(command)
        if not response(board, "@pocket.config ").startswith("saved"):
            raise BoardError("Device configuration was rejected; no credentials were printed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--pairing", type=Path)
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--storage-test", action="store_true", help="Verify a temporary capture file on device storage")
    args = parser.parse_args()
    try:
        with Board(args.port or pick_port()) as board:
            board.write(b"w")
            if args.pairing:
                provision(board, json.loads(args.pairing.read_text()))
                print("Companion provisioned. Restart the device to apply it.")
            if args.disable:
                board.write_line("pocket.url=")
                if not response(board, "@pocket.config ").startswith("saved"):
                    raise BoardError("Could not disable companion")
                print("Restart to return to normal Muse voice.")
            if args.storage_test:
                board.write_line("pocket.storage_test")
                result = json.loads(response(board, "@pocket.storage ", timeout=20))
                print(json.dumps(result))
                if not result.get("ok"):
                    raise BoardError("Device storage check failed")
            board.write_line("pocket.status")
            print(response(board, "@pocket "))
    except (OSError, ValueError, KeyError, BoardError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
