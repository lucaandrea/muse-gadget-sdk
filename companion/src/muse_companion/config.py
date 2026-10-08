from __future__ import annotations

import os
import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs


def grep_access_token(value: str) -> str:
    value = value.strip()
    # Replit's copy button may include this query-string prefix. Extract the
    # credential locally and always send it as a header, never as a URL.
    if value.startswith("?project-protection-bypass="):
        value = parse_qs(value[1:]).get("project-protection-bypass", [""])[0]
    if any(char.isspace() for char in value):
        raise ValueError("Invalid GREP_EXTERNAL_ACCESS_TOKEN format")
    return value


def read_env(path: Path) -> dict[str, str]:
    """Parse dotenv as data, never as shell code; environment overrides the file."""
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            key = key.strip().removeprefix("export ")
            if not sep or not key or key.startswith("#"):
                continue
            words = shlex.split(value, comments=True)
            if len(words) == 1:
                result[key] = words[0]
    return result


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path("data"))
    api_key: str = field(default="", repr=False)
    grep_external_token: str = field(default="", repr=False)
    grep_model: str = "gpt-6.1-sol"
    model: str = "gpt-6.1-sol"
    small_model: str = "gpt-6-luna"
    deep_model: str = "gpt-6-astra"
    live_model: str = "gpt-live-1"
    transcription_model: str = "gpt-transcribe"
    timezone: str = "America/Los_Angeles"
    voice: str = "marin"
    live_max_seconds: int = 300
    daily_live_minutes: int = 30
    daily_model_calls: int = 300
    quiet_start: int = 22
    quiet_end: int = 8
    decisions_enabled: bool = False
    integrations_file: Path | None = None
    integration_credentials: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, env_path: Path | None = None) -> Settings:
        env = read_env(env_path or Path("../.env")) | dict(os.environ)
        result = cls(
            data_dir=Path(env.get("MUSE_DATA_DIR", "data")).resolve(),
            api_key=env.get("OPENAI_API_KEY", ""),
            grep_external_token=grep_access_token(env.get("GREP_EXTERNAL_ACCESS_TOKEN", "")),
            grep_model=env.get("GREP_ANSWER_MODEL", "gpt-6.1-sol"),
            model=env.get("MUSE_MODEL", "gpt-6.1-sol"),
            timezone=env.get("MUSE_TIMEZONE", "America/Los_Angeles"),
            decisions_enabled=env.get("MUSE_DECISIONS", "0") == "1",
            live_max_seconds=int(env.get("MUSE_LIVE_MAX_SECONDS", "300")),
            daily_live_minutes=int(env.get("MUSE_DAILY_LIVE_MINUTES", "30")),
            daily_model_calls=int(env.get("MUSE_DAILY_MODEL_CALLS", "300")),
            integrations_file=Path(env["MUSE_INTEGRATIONS"]).resolve() if env.get("MUSE_INTEGRATIONS") else None,
        )
        ZoneInfo(result.timezone)
        if not 10 <= result.live_max_seconds <= 1800 or result.daily_live_minutes < 1 or result.daily_model_calls < 1:
            raise ValueError("Invalid usage limits")
        result.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if result.integrations_file is None and (result.data_dir / "integrations.json").exists():
            result.integrations_file = result.data_dir / "integrations.json"
        if result.integrations_file and result.integrations_file.exists():
            commands = json.loads(result.integrations_file.read_text())
            names = {command.get("token_env") for command in commands.values()}
            result.integration_credentials = {name: env[name] for name in names if name and env.get(name)}
        return result
