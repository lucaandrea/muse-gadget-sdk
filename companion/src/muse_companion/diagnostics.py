"""Connection observations are separate from account and credential state."""
import time

from fastapi import Depends
from pydantic import Field

from .models import StrictModel
from .store import new_id


class DeviceHealth(StrictModel):
    firmware: str = Field(default="", max_length=80)
    wifi_connected: bool | None = None
    muse_paired: bool | None = None
    battery_percent: int | None = Field(default=None, ge=0, le=100)
    charging: bool | None = None
    free_internal_bytes: int | None = Field(default=None, ge=0)
    pocket_storage_free: int | None = Field(default=None, ge=0)
    profile: str = Field(default="", max_length=20)
    next_sync: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Diagnostics:
    def __init__(self, store, settings, grep):
        self.store, self.settings, self.grep = store, settings, grep
        self.boot = new_id()

    def observe(self, identity, session, *, connected=True, report=None):
        prior = self.store.setting("device_health:" + identity, {})
        # An old socket closing must not mark its replacement disconnected.
        if not connected and prior.get("session") != session:
            return
        if report is not None:
            prior["reported"] = DeviceHealth.model_validate(report).model_dump()
            prior["reported_at"] = time.time()
        prior.update(session=session, connected=connected, last_seen=time.time(), boot=self.boot)
        self.store.set_setting("device_health:" + identity, prior)

    def compatibility(self):
        probe = new_id()
        with self.store.transaction():
            self.store.set_setting("storage_probe", probe)
        return {"protocol": 1, "storage_schema": 5, "storage_ready": self.store.setting("storage_probe") == probe,
                "features": ["capture_modes_v1", "durable_capture_v1", "action_receipts_v1", "studio_v1", "health_v1", "notebook_segments_v1"],
                "boot": self.boot}

    def pocket_accounts(self):
        """A bounded recovery summary; never send grant details to the device."""
        grep = self.grep.status()
        lines = ["Grep: " + ("session available" if grep["authorized"] else "connect in Tools")]
        now = time.time()
        names = {"slack": "Slack", "linear": "Linear", "google_calendar": "Calendar"}
        for account in self.work_accounts.status() if hasattr(self, "work_accounts") else []:
            if not account.get("oauth_configured"):
                state = "setup needed in Tools"
            elif not account.get("connected"):
                state = "connect in Tools"
            elif account.get("error") or (account.get("expires_at") and account["expires_at"] <= now):
                state = "check access in Tools"
            elif not account.get("destination"):
                state = "choose destination in Tools"
            elif now - account.get("checked_at", 0) > 900:
                state = "linked; check access in Tools"
            else:
                state = "access checked"
            lines.append(names[account["service"]] + ": " + state)
        return {"summary": "\n".join(lines), "tools_url": self.settings.public_url + "/#view=tools" if self.settings.public_url else ""}

    def snapshot(self):
        now, devices = time.time(), []
        for device in self.store.rows("SELECT id,name,revoked FROM devices WHERE role='device'"):
            observed = self.store.setting("device_health:" + device["id"], {})
            fresh = observed.get("boot") == self.boot and now - observed.get("last_seen", 0) < 90
            connected = bool(observed.get("connected") and fresh and not device["revoked"])
            report = observed.get("reported", {})
            report_fresh = connected and now - observed.get("reported_at", 0) < 90
            devices.append({**device, "companion": "connected" if connected else "disconnected", "last_seen": observed.get("last_seen"),
                "wifi": "connected" if connected else "unknown", "muse_pairing": ("paired" if report.get("muse_paired") else "not_paired") if report_fresh and report.get("muse_paired") is not None else "unknown",
                "reported": report, "reported_at": observed.get("reported_at"), "report_fresh": report_fresh,
                "recovery": "Re-provision a companion token" if device["revoked"] else "Check device Wi-Fi and the companion URL" if not connected else "Complete Muse account pairing on your phone" if report_fresh and report.get("muse_paired") is False else ""})
        return {"companion": {"state": "available", "protocol": 1, "storage_schema": 5, "storage": self.store.backend, "hosted": self.settings.hosted},
                "openai": {"configured": bool(self.settings.api_key), "access_verified": False, "note": "Credential presence only. Run the backend doctor to check model access."},
                "work_account": self.grep.status(), "work_accounts": self.work_accounts.status() if hasattr(self, "work_accounts") else [], "devices": devices,
                "note": "Muse SDK authorization, Muse account pairing, companion pairing, and work OAuth are separate."}


def mount_diagnostics(app, diagnostics, owner):
    @app.get("/api/diagnostics")
    async def status(user=Depends(owner)):
        return diagnostics.snapshot()

    @app.post("/api/compatibility")
    async def compatibility(user=Depends(owner)):
        return diagnostics.compatibility()
