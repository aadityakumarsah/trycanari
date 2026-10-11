"""Encryption at rest for system prompts, tool definitions and canary secrets.

AES-256-GCM under the key in ENCRYPTION_KEY, which never reaches the
database. A stolen database dump cannot read these columns.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"
_NONCE_BYTES = 12
KEY_BYTES = 32


class DecryptionError(RuntimeError):
    """The ciphertext is damaged, was made with another key, or belongs to another column."""


def generate_key() -> str:
    """A new random key in the form ENCRYPTION_KEY expects."""
    return base64.b64encode(AESGCM.generate_key(KEY_BYTES * 8)).decode()


class Cipher:
    def __init__(self, key_base64: str) -> None:
        key = base64.b64decode(key_base64, validate=True)
        if len(key) != KEY_BYTES:
            raise ValueError(f"the encryption key must be {KEY_BYTES} bytes, base64-encoded")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes, *, purpose: str) -> bytes:
        """Encrypt for one named use. The same `purpose` is needed to decrypt, so a
        ciphertext copied into a different column does not decrypt there."""
        nonce = os.urandom(_NONCE_BYTES)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext, purpose.encode())

    def decrypt(self, ciphertext: bytes, *, purpose: str) -> bytes:
        if ciphertext[:1] != _VERSION or len(ciphertext) < 1 + _NONCE_BYTES:
            raise DecryptionError("unrecognised ciphertext")
        nonce, body = ciphertext[1 : 1 + _NONCE_BYTES], ciphertext[1 + _NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, body, purpose.encode())
        except InvalidTag:
            raise DecryptionError("ciphertext failed authentication") from None

    def encrypt_json(self, value: Any, *, purpose: str) -> bytes:
        return self.encrypt(json.dumps(value).encode(), purpose=purpose)

    def decrypt_json(self, ciphertext: bytes, *, purpose: str) -> Any:
        return json.loads(self.decrypt(ciphertext, purpose=purpose))
