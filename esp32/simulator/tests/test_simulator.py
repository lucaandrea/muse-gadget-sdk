#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Headless smoke and deterministic framebuffer tests for the UI simulator."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

WIDTH = HEIGHT = 412
HERE = Path(__file__).resolve().parent
SCENARIOS = tuple(sorted((HERE / "scenarios").glob("*.txt")))


def read_ppm(path: Path, size: int = WIDTH) -> bytes:
    raw = path.read_bytes()
    header = f"P6\n{size} {size}\n255\n".encode()
    assert raw.startswith(header), f"{path}: wrong PPM header"
    pixels = raw[len(header) :]
    assert len(pixels) == size * size * 3, f"{path}: truncated framebuffer"
    assert len(set(pixels)) > 8, f"{path}: framebuffer has too few colours"
    return pixels


def render(binary: Path, scenario: Path, output: Path, board: str = "watcher") -> tuple[str, subprocess.CompletedProcess[str]]:
    env = {**os.environ, "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"}
    proc = subprocess.run(
        [
            str(binary),
            "--headless",
            "--board",
            board,
            "--scenario",
            str(scenario),
            "--run-ms",
            "200",
            "--screenshot",
            str(output),
        ],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    assert proc.returncode == 0, f"{scenario.name}:\n{proc.stdout}\n{proc.stderr}"
    pixels = read_ppm(output, 466 if board == "waveshare-s3-175c" else WIDTH)
    size = 466 if board == "waveshare-s3-175c" else WIDTH
    # A flat framebuffer can conceal content that the real circular bezel cuts off.
    radius2 = (size / 2 - 6) ** 2
    for y in range(size):
        for x in range(size):
            if (x - size / 2) ** 2 + (y - size / 2) ** 2 > radius2:
                i = (y * size + x) * 3
                assert max(pixels[i:i+3]) <= 8, f"{scenario.name}: content outside safe circle at {x},{y}"
    return hashlib.sha256(pixels).hexdigest(), proc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    assert binary.is_file(), binary
    assert SCENARIOS, "no simulator scenarios found"

    with tempfile.TemporaryDirectory(prefix="muse-simulator-test-") as tmp:
        tmp_path = Path(tmp)
        hashes: dict[str, str] = {}
        for scenario in SCENARIOS:
            first, _ = render(binary, scenario, tmp_path / f"{scenario.stem}-1.ppm")
            second, _ = render(binary, scenario, tmp_path / f"{scenario.stem}-2.ppm")
            assert first == second, f"{scenario.name}: framebuffer is not deterministic"
            hashes[scenario.stem] = first

        assert len(set(hashes.values())) == len(hashes), f"scenarios rendered identically: {hashes}"

        # The 1.75C has a larger circle and different physical-button hints.
        # Exercise production layout and screenshot allocation at its real size.
        for scenario in SCENARIOS:
            first, _ = render(binary, scenario, tmp_path / "waveshare-1.ppm", "waveshare-s3-175c")
            second, _ = render(binary, scenario, tmp_path / "waveshare-2.ppm", "waveshare-s3-175c")
            assert first == second, f"466px {scenario.name}: framebuffer is not deterministic"

        # Real pointer events hit the round board's speaker at (114,94).
        # Quick taps and long holds must each toggle exactly once on release.
        touch = tmp_path / "touch.txt"
        touch.write_text("face=idle\nspeaker=true\nadvance=200\n"
                         "touch=114,94,down\nadvance=80\nexpect_speaker=true\n"
                         "touch=114,94,up\nadvance=80\nexpect_speaker=false\n"
                         "touch=114,94,down\nadvance=800\nexpect_speaker=false\n"
                         "touch=114,94,up\nadvance=80\nexpect_speaker=true\n"
                         "touch=114,94,down\nadvance=80\n"
                         "touch=240,240,move\nadvance=80\ntouch=240,240,up\n"
                         "advance=80\nexpect_speaker=true\n")
        render(binary, touch, tmp_path / "touch.ppm", "waveshare-s3-175c")

        # Read at your own pace; explicit navigation pauses voice following.
        reader = tmp_path / "reader.txt"
        reader.write_text((HERE / "scenarios/refined_reading.txt").read_text() +
            "expect_ui_reading=1\nexpect_ui_manual=1\nexpect_ui_page=0\n"
            "touch=329,396,down\nadvance=80\ntouch=329,396,up\nadvance=200\nexpect_ui_page=1\n"
            "face=idle\nadvance=400\nexpect_ui_answer=1\nexpect_ui_page=1\n"
            "touch=344,124,down\nadvance=80\ntouch=344,124,up\nadvance=200\nexpect_ui_manual=0\nexpect_ui_page=0\n"
            "face=listening\nadvance=200\nexpect_ui_answer=0\nexpect_ui_reading=0\n")
        render(binary, reader, tmp_path / "reader.ppm", "waveshare-s3-175c")

        # Exercise the real Display settings and keep visual/audio preferences separate.
        preferences = tmp_path / "preferences.txt"
        preferences.write_text("face=idle\nadvance=200\nview=display\nadvance=300\n"
            "touch=356,114,down\nadvance=80\ntouch=356,114,up\nadvance=200\n"
            "expect_character=false\nexpect_speaker=true\n"
            "touch=356,241,down\nadvance=80\ntouch=356,241,up\nadvance=200\n"
            "expect_reduced_motion=true\nview=face\nadvance=300\n")
        render(binary, preferences, tmp_path / "preferences.ppm", "waveshare-s3-175c")

        # Showing shutdown must not lock subsequent preview state selections.
        after_off = tmp_path / "after-off.txt"
        after_off.write_text("face=off\n" + (HERE / "scenarios/listening.txt").read_text())
        recovered, _ = render(binary, after_off, tmp_path / "after-off.ppm")
        assert recovered == hashes["listening"], "Off prevented the next preview state"

        invalid = tmp_path / "invalid.txt"
        for setting in ("face=definitely-not-a-mode", "level=nan"):
            invalid.write_text(f"{setting}\n")
            proc = subprocess.run(
                [str(binary), "--headless", "--scenario", str(invalid)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            assert proc.returncode == 2
            assert "unsupported or invalid setting" in proc.stderr

    for name, digest in sorted(hashes.items()):
        print(f"{name}: {digest}")


if __name__ == "__main__":
    main()
