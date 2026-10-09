from __future__ import annotations

import os
import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse


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
    database_url: str = field(default="", repr=False)
    owner_token: str = field(default="", repr=False)
    hosted: bool = False
    public_url: str = ""
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
    work_oauth: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, env_path: Path | None = None) -> Settings:
        env = read_env(env_path or Path("../.env")) | dict(os.environ)
        result = cls(
            data_dir=Path(env.get("MUSE_DATA_DIR", "data")).resolve(),
            api_key=env.get("OPENAI_API_KEY", ""),
            database_url=env.get("DATABASE_URL", ""),
            owner_token=env.get("MUSE_OWNER_TOKEN", ""),
            work_oauth={name: {"client_id": env.get(prefix + "_CLIENT_ID", ""), "client_secret": env.get(prefix + "_CLIENT_SECRET", "")}
                        for name, prefix in (("slack", "SLACK"), ("linear", "LINEAR"), ("google_calendar", "GOOGLE"))},
            hosted=env.get("MUSE_HOSTED", "0") == "1",
            public_url=env.get("MUSE_PUBLIC_URL", "").rstrip("/"),
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
        if result.public_url:
            url = urlparse(result.public_url)
            local = url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")
            if (url.scheme != "https" and not local) or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
                raise ValueError("MUSE_PUBLIC_URL must be an HTTPS origin, or a localhost HTTP origin for development")
        if result.hosted and (not result.database_url or len(result.owner_token) < 32):
            raise ValueError("Hosted mode requires DATABASE_URL and a stable MUSE_OWNER_TOKEN of at least 32 characters")
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
