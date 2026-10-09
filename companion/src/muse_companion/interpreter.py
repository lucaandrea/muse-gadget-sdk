"""Explicit two-language controls for the pocket, separate from assistant tools."""
from .models import Button, Card
from .store import new_id

LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
             "pt": "Portuguese", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "hi": "Hindi"}


class Interpreter:
    def __init__(self, store):
        self.store = store

    def current(self, device):
        return self.store.setting("interpreter:" + device)

    def choose_device(self, caller):
        device = self.store.one("SELECT id FROM devices WHERE id=? AND role='device' AND revoked=0", (caller,))
        if device:
            return device["id"]
        if not self.store.one("SELECT id FROM devices WHERE id=? AND role='owner' AND revoked=0", (caller,)):
            raise ValueError("Choose an existing paired device")
        devices = self.store.rows("SELECT id FROM devices WHERE role='device' AND revoked=0")
        if len(devices) != 1:
            raise ValueError("Choose a pocket device in the companion; there is not exactly one paired device")
        return devices[0]["id"]

    def start(self, caller, mine, theirs):
        if mine not in LANGUAGES or theirs not in LANGUAGES or mine == theirs:
            raise ValueError("Choose two different supported languages")
        device = self.choose_device(caller)
        if "capture_modes_v1" not in self.store.setting("capabilities:" + device, []):
            raise ValueError("Connect a pocket running the interpreter firmware before starting device translation")
        session = {"device_id": device, "session_id": new_id(), "mine": mine, "theirs": theirs, "target": theirs, "last_text": ""}
        self.store.set_setting("interpreter:" + device, session)
        self.store.event("interpreter.changed", {"device_id": device})
        return self.card(device)

    def action(self, identity, action):
        device = identity.removeprefix("interpret:")
        session = self.current(device)
        if not identity.startswith("interpret:") or not session:
            raise ValueError("Interpreter session is no longer active")
        if action == "end_interpret":
            self.store.set_setting("interpreter:" + device, None)
        elif action in ("language_a", "language_b"):
            session["target"] = session["theirs"] if action == "language_a" else session["mine"]
            self.store.set_setting("interpreter:" + device, session)
        else:
            raise ValueError("Invalid interpreter control")
        self.store.event("interpreter.changed", {"device_id": device})
        card = self.card(device)
        return {"ok": True, "state": "assistant" if action == "end_interpret" else "interpreter",
                "card": card.model_dump() if card else None}

    def mode(self, device):
        session = self.current(device)
        return {"type": "capture.mode", "mode": "translate" if session else "recorded",
                "language": session["target"] if session else ""}

    def card(self, device):
        session = self.current(device)
        if not session:
            return None
        mine, theirs = LANGUAGES[session["mine"]], LANGUAGES[session["theirs"]]
        body = session["last_text"] or "Tap your language, then hold the talk button. Release to hear the translation. Tap End to return to the assistant."
        return Card(id="interpret:" + device, kind="translation", title=f"{mine} / {theirs}", body=body,
                    source="Translating to " + LANGUAGES[session["target"]], status="interpreter",
                    buttons=[Button(id=device, label=mine, action="language_a"), Button(id=device, label=theirs, action="language_b"), Button(id=device, label="End", action="end_interpret")])

    def remember_result(self, device, source, text, target):
        session = self.current(device)
        if not session:
            return Card(id=new_id(), kind="translation", title="Translation", body=(source[:200] + "\n\n" + text[:200]), source="AI translation · " + target)
        session["last_text"] = source[:180] + "\n\nTo " + LANGUAGES.get(target, target) + ":\n" + text[:180]
        self.store.set_setting("interpreter:" + device, session)
        return self.card(device)
