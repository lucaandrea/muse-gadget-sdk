from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx


class Integrations:
    """Only operator-configured commands; models cannot choose hosts or shell code."""
    def __init__(self, path: Path | None, credentials: dict[str, str] | None = None):
        self.commands = json.loads(path.read_text()) if path and path.exists() else {}
        self.credentials = credentials or {}
        for name, command in self.commands.items():
            if command.get("kind") not in ("http", "process"):
                raise ValueError(f"Invalid integration kind: {name}")
            if command["kind"] == "http":
                url = urlparse(command["url"])
                if url.scheme not in ("https", "http") or url.username or url.password:
                    raise ValueError(f"Invalid integration URL: {name}")
            elif not command.get("argv") or not Path(command["cwd"]).is_absolute():
                raise ValueError(f"Process needs fixed argv and an absolute cwd: {name}")

    def catalog(self) -> list[dict]:
        return [{"name": name, "description": c.get("description", name), "kind": c["kind"],
                 "fields": c.get("fields", []), "read_only": bool(c.get("read_only", False))} for name, c in self.commands.items()]

    def validate(self, name: str, arguments: dict):
        if name not in self.commands:
            raise ValueError("This integration is not configured. Connect it in the companion settings first.")
        command = self.commands[name]
        allowed = set(command.get("fields", []))
        if set(arguments) - allowed or any(not isinstance(v, (str, int, float, bool)) for v in arguments.values()):
            raise ValueError("Invalid integration arguments")
        if command["kind"] == "process" and arguments:
            raise ValueError("Computer workflows use fixed commands and accept no shell arguments")
        return command

    async def execute(self, name: str, arguments: dict, operation_id: str) -> dict:
        command = self.validate(name, arguments)
        if command["kind"] == "http":
            headers = {"Idempotency-Key": operation_id}
            if command.get("token_env"):
                token = self.credentials.get(command["token_env"]) or os.environ.get(command["token_env"])
                if not token:
                    raise ValueError("Integration credential is not configured")
                headers["Authorization"] = "Bearer " + token
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                response = await client.request(command.get("method", "POST"), command["url"], headers=headers, json=arguments or None)
                if response.is_error or response.is_redirect:
                    raise RuntimeError(f"Integration returned HTTP {response.status_code}; verify its external state")
                # The destination is fixed by the owner, never by a model or tool result.
                return {"status": response.status_code, "result": response.text[:12000]}
        # This is an opt-in local runner, not a sandbox. Restrict workflows to
        # trusted, read-only commands; never permit an LLM to generate argv.
        env = {k: os.environ[k] for k in ("PATH", "LANG", "TMPDIR", "DEVELOPER_DIR") if k in os.environ}
        process = await asyncio.create_subprocess_exec(*command["argv"], cwd=command["cwd"], env=env,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output = bytearray()
        async def drain():
            while chunk := await process.stdout.read(4096):
                if len(output) < 64000:
                    output.extend(chunk[:64000-len(output)])
            return await process.wait()
        try:
            code = await asyncio.wait_for(drain(), min(int(command.get("timeout", 120)), 300))
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise RuntimeError("Computer workflow timed out or was cancelled") from None
        return {"exit_code": code, "output": output.decode(errors="replace"), "truncated": len(output) == 64000}
