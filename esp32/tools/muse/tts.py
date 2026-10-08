#!/usr/bin/env python3
"""Provision OpenAI speech over USB without printing or compiling the API key.

    python tools/muse/tts.py --env ../.env --port /dev/cu.usbmodem2101 --test
    python tools/muse/tts.py --port /dev/cu.usbmodem2101 --status

The .env is parsed as data: no shell expansion or execution. Firmware keeps
the key in NVS, so normal flashes preserve it. --test speaks a fixed phrase
through the same synthesis, resampling, caption and speaker path as replies.
"""
import argparse
import json
from pathlib import Path
import shlex
import time

from chat import Board, BoardError, pick_port


def read_key(path):
    key = None
    for line in Path(path).read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() in ("OPENAI_API_KEY", "export OPENAI_API_KEY"):
            try:
                values = shlex.split(value, comments=True, posix=True)
            except ValueError:
                raise BoardError("OPENAI_API_KEY has invalid quoting") from None
            if len(values) != 1:
                raise BoardError("OPENAI_API_KEY must have one nonempty value")
            key = values[0]
    if not key or len(key) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise BoardError("OPENAI_API_KEY is missing or invalid")
    return key


def wait_line(board, prefix, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = board.read_line(deadline)
        if line and line.startswith(prefix):
            return line[len(prefix):]
    raise BoardError("No speech response from the board; check its firmware and USB port")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--env", type=Path)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    try:
        key = read_key(args.env) if args.env else None
        with Board(args.port or pick_port()) as board:
            board.write(b"w")
            if key:
                board.write_line("openai.key=" + key)
                del key
                if wait_line(board, "@speech.key ") != "saved":
                    raise BoardError("The board could not save the speech key")
                print("OpenAI speech key saved privately on the device.")
            board.write_line("speech.status")
            status = json.loads(wait_line(board, "@speech "))
            print(json.dumps(status))
            if args.test:
                if not status.get("configured"):
                    raise BoardError("Provision the speech key with --env before testing")
                board.write_line("speech.test")
                print("Speaker test requested. The speaker must be unmuted with volume above zero.")
    except (BoardError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
