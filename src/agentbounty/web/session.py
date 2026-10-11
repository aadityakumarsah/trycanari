"""The sponsor's session: their API key, encrypted in a cookie.

There is no session table. The cookie holds the key sealed with the service's
encryption key, so it is opaque to the browser and useless if copied to
another deployment, and it expires on its own.
"""

from __future__ import annotations

import base64
import binascii
import time

from agentbounty.services.crypto import Cipher, DecryptionError

COOKIE = "ab_session"
MAX_AGE_SECONDS = 12 * 60 * 60
_PURPOSE = "web.session"


def seal(cipher: Cipher, api_key: str) -> str:
    payload = {"key": api_key, "issued": int(time.time())}
    return base64.urlsafe_b64encode(cipher.encrypt_json(payload, purpose=_PURPOSE)).decode()


def unseal(cipher: Cipher, cookie: str | None) -> str | None:
    """The API key in the cookie, or None if it is missing, forged or expired."""
    if not cookie:
        return None
    try:
        payload = cipher.decrypt_json(base64.urlsafe_b64decode(cookie), purpose=_PURPOSE)
        if time.time() - int(payload["issued"]) > MAX_AGE_SECONDS:
            return None
        return str(payload["key"])
    except (DecryptionError, binascii.Error, ValueError, KeyError, TypeError):
        return None
