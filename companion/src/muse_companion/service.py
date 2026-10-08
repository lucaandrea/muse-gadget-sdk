"""Install an independent per-user runtime, outside protected project folders."""
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from .config import read_env


def service_values(settings, data, env):
    # Preserve credentials/configuration added to the installed service by an
    # operator or an account-connection flow. A code upgrade is not a reset.
    values = read_env(env)
    values.update({"OPENAI_API_KEY": settings.api_key, "MUSE_TIMEZONE": settings.timezone,
                   "MUSE_MODEL": settings.model, "MUSE_DATA_DIR": str(data),
                   "MUSE_DECISIONS": "1" if settings.decisions_enabled else "0",
                   "MUSE_DAILY_MODEL_CALLS": str(settings.daily_model_calls),
                   "MUSE_DAILY_LIVE_MINUTES": str(settings.daily_live_minutes),
                   "MUSE_LIVE_MAX_SECONDS": str(settings.live_max_seconds)})
    if settings.grep_external_token:
        values["GREP_EXTERNAL_ACCESS_TOKEN"] = settings.grep_external_token
    values["GREP_ANSWER_MODEL"] = settings.grep_model
    values.update(settings.integration_credentials)
    return values


def install(settings, port):
    if sys.platform != "darwin":
        raise ValueError("Use Docker or your host service manager on this platform")
    project = Path.cwd()
    if not (project / "pyproject.toml").exists():
        raise ValueError("Run install-service from the companion directory")
    root = Path.home() / "Library/Application Support/MuseCompanion"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = root / "data"
    data.mkdir(exist_ok=True, mode=0o700)
    label = "ai.muse.pocket-companion"
    plist = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain + "/" + label], capture_output=True)
    runtime = root / "venv"
    python = runtime / "bin/python"
    uv = shutil.which("uv")
    if uv:
        if not python.exists():
            subprocess.run([uv, "venv", "--python", sys.executable, str(runtime)], check=True)
        subprocess.run([uv, "pip", "install", "--python", str(python), "--reinstall-package", "muse-companion", str(project)], check=True)
    else:
        if not python.exists():
            subprocess.run([sys.executable, "-m", "venv", str(runtime)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", str(project)], check=True)
    # First installation migrates state using SQLite's backup API; upgrades keep
    # the running service's own database, never overwrite it with a stale copy.
    if not (data / "muse.sqlite3").exists():
        source = sqlite3.connect(settings.data_dir / "muse.sqlite3")
        target = sqlite3.connect(data / "muse.sqlite3")
        source.backup(target)
        source.close(); target.close()
        for item in settings.data_dir.iterdir():
            if item.name.startswith("muse.sqlite3") or item.name == "service.log":
                continue
            destination = data / item.name
            if item.is_dir(): shutil.copytree(item, destination, dirs_exist_ok=True)
            else: shutil.copy2(item, destination)
    cert, key = data / "local-cert.pem", data / "local-key.pem"
    if not cert.exists() or not key.exists():
        raise ValueError("Create local TLS before installing the service")
    env = root / "backend.env"
    values = service_values(settings, data, env)
    import shlex
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        os.fchmod(out.fileno(), 0o600)
        out.write("\n".join(k + "=" + shlex.quote(v) for k, v in values.items()) + "\n")
    if settings.integrations_file:
        shutil.copy2(settings.integrations_file, data / "integrations.json")
    config = {"Label": label, "WorkingDirectory": str(root), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
              "ProgramArguments": [str(python), "-m", "muse_companion.cli", "--env", str(env), "serve", "--host", "0.0.0.0", "--port", str(port), "--cert", str(cert), "--key", str(key)],
              "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
              "StandardOutPath": str(data / "service.log"), "StandardErrorPath": str(data / "service.log")}
    plist.parent.mkdir(parents=True, exist_ok=True)
    with plist.open("wb") as out: plistlib.dump(config, out)
    plist.chmod(0o600)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    return root
