"""Durable credentials encrypted with the deployment's stable owner secret."""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, settings, store, namespace):
        self.settings, self.store, self.namespace = settings, store, namespace

    def cipher(self):
        path = self.settings.data_dir / "owner-token"
        secret = self.settings.owner_token or (path.read_text().strip() if path.exists() else "")
        if not secret:
            raise ValueError("Configure companion owner access before connecting an account")
        key = hashlib.sha256(("muse-vault-v1:" + self.namespace + ":" + secret).encode()).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    def read(self, key):
        value = self.store.setting("vault:" + self.namespace + ":" + key)
        if not value:
            return None
        try:
            return json.loads(self.cipher().decrypt(value.encode()))
        except (InvalidToken, ValueError, TypeError):
            raise ValueError("Saved account access could not be read. Reconnect this account") from None

    def write(self, key, value):
        self.store.set_setting("vault:" + self.namespace + ":" + key,
            self.cipher().encrypt(json.dumps(value).encode()).decode() if value is not None else None)
