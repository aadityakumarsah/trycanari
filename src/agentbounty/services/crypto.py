"""Envelope encryption at rest for system prompts, tool definitions, canary secrets
and replay transcripts.

Each value is encrypted under its own random data key (AES-256-GCM), and that
data key is wrapped under the master key in SECRET_ENCRYPTION_KEY, which never
reaches the database. A stolen database dump holds only ciphertext and
wrapped keys; rotating the master key means re-wrapping data keys, not
re-encrypting every value.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
_NONCE_BYTES = 12
_TAG_BYTES = 16
_WRAPPED_KEY_BYTES = KEY_BYTES + _TAG_BYTES
# 1: the value encrypted directly under the master key (written before envelopes).
# 2: an envelope.
_DIRECT, _ENVELOPE = b"\x01", b"\x02"


class DecryptionError(RuntimeError):
    """The ciphertext is damaged, was made with another key, or belongs to another column."""


def generate_key() -> str:
    """A new random key in the form SECRET_ENCRYPTION_KEY expects."""
    return base64.b64encode(AESGCM.generate_key(KEY_BYTES * 8)).decode()


class Cipher:
    def __init__(self, key_base64: str) -> None:
        key = base64.b64decode(key_base64, validate=True)
        if len(key) != KEY_BYTES:
            raise ValueError(f"the encryption key must be {KEY_BYTES} bytes, base64-encoded")
        self._master = AESGCM(key)

    def encrypt(self, plaintext: bytes, *, purpose: str) -> bytes:
        """Encrypt for one named use. The same `purpose` is needed to decrypt, so a
        ciphertext copied into a different column does not decrypt there."""
        bound = purpose.encode()
        data_key = AESGCM.generate_key(KEY_BYTES * 8)
        key_nonce, nonce = os.urandom(_NONCE_BYTES), os.urandom(_NONCE_BYTES)
        return (
            _ENVELOPE
            + key_nonce
            + self._master.encrypt(key_nonce, data_key, bound)
            + nonce
            + AESGCM(data_key).encrypt(nonce, plaintext, bound)
        )

    def decrypt(self, ciphertext: bytes, *, purpose: str) -> bytes:
        bound = purpose.encode()
        version, body = ciphertext[:1], ciphertext[1:]
        try:
            if version == _ENVELOPE and len(body) >= 2 * _NONCE_BYTES + _WRAPPED_KEY_BYTES:
                key_nonce, body = body[:_NONCE_BYTES], body[_NONCE_BYTES:]
                wrapped, body = body[:_WRAPPED_KEY_BYTES], body[_WRAPPED_KEY_BYTES:]
                data_key = self._master.decrypt(key_nonce, wrapped, bound)
                return AESGCM(data_key).decrypt(body[:_NONCE_BYTES], body[_NONCE_BYTES:], bound)
            if version == _DIRECT and len(body) >= _NONCE_BYTES:
                return self._master.decrypt(body[:_NONCE_BYTES], body[_NONCE_BYTES:], bound)
        except InvalidTag:
            raise DecryptionError("ciphertext failed authentication") from None
        raise DecryptionError("unrecognised ciphertext")

    def encrypt_json(self, value: Any, *, purpose: str) -> bytes:
        return self.encrypt(json.dumps(value).encode(), purpose=purpose)

    def decrypt_json(self, ciphertext: bytes, *, purpose: str) -> Any:
        return json.loads(self.decrypt(ciphertext, purpose=purpose))
