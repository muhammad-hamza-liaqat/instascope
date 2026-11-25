"""Encrypt secrets at rest (Instagram session cookies) and fingerprint passwords.

Key source: SECRET_KEY env var, or an auto-generated `storage/.secret_key` (chmod 600).
If the key is lost, stored sessions can't be decrypted; accounts simply log in again.
"""

import base64
import hashlib
import hmac
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)
        self._hmac_key = hashlib.sha256(b"fingerprint:" + key).digest()

    @classmethod
    def from_settings(cls, secret_key: str | None, storage_dir: Path) -> "SecretBox":
        if secret_key:
            return cls(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))
        path = storage_dir / ".secret_key"
        if path.exists():
            return cls(path.read_bytes().strip())
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        log.warning("Generated %s; back it up (or set SECRET_KEY) to keep stored sessions", path)
        return cls(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return None

    def fingerprint(self, value: str) -> str:
        """Keyed hash, used to detect password changes without storing the password."""
        return hmac.new(self._hmac_key, value.encode(), hashlib.sha256).hexdigest()
