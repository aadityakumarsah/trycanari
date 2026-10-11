"""Profile pictures, kept at Cloudinary.

Each account has exactly one picture there, at an address made from the
account's ID. Uploading a new one overwrites the old in place, so nothing is
ever left behind to pay storage for, and removing the picture deletes it.

Every picture is cut to a 400-pixel square and re-encoded as it arrives: that
is the only version stored, it is small, and whatever else the uploaded file
carried (location data, anything hidden in it) does not survive.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from agentbounty.services.errors import ServiceError

MAX_BYTES = 4 * 1024 * 1024
FOLDER = "trycanari/avatars"
# Stored once, at this size: a square around the face if there is one.
STORED_AS = "c_fill,g_face,h_400,w_400,q_auto:good"

# The first bytes of the formats that are accepted.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def image_type(data: bytes) -> str | None:
    """What kind of image the bytes are, going by the bytes and not by what was claimed."""
    for start, kind in _SIGNATURES:
        if data.startswith(start):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True)
class Cloudinary:
    cloud: str
    api_key: str
    api_secret: str

    @classmethod
    def from_url(cls, url: str) -> Cloudinary:
        """From `cloudinary://<api_key>:<api_secret>@<cloud_name>`."""
        parts = urlsplit(url)
        if parts.scheme != "cloudinary" or not (
            parts.username and parts.password and parts.hostname
        ):
            raise ValueError("must look like cloudinary://<api_key>:<api_secret>@<cloud_name>")
        return cls(cloud=parts.hostname, api_key=parts.username, api_secret=parts.password)

    def sign(self, params: dict[str, str]) -> str:
        """Cloudinary's request signature: the parameters in order, then the secret."""
        joined = "&".join(f"{key}={params[key]}" for key in sorted(params))
        return hashlib.sha1((joined + self.api_secret).encode(), usedforsecurity=False).hexdigest()

    def public_id(self, account_id: uuid.UUID) -> str:
        return f"{FOLDER}/{account_id}"

    def _signed(self, params: dict[str, str]) -> dict[str, str]:
        params = {**params, "timestamp": str(int(time.time()))}
        return {**params, "api_key": self.api_key, "signature": self.sign(params)}

    async def upload(
        self,
        account_id: uuid.UUID,
        data: bytes,
        content_type: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> str:
        """Store the picture, replacing the account's old one, and return its address."""
        fields = self._signed(
            {
                "public_id": self.public_id(account_id),
                "overwrite": "true",
                # Drop copies of the old picture that Cloudinary's network has cached.
                "invalidate": "true",
                "transformation": STORED_AS,
                "format": "jpg",
            }
        )
        answer = await self._post(
            "upload", fields, transport, {"file": ("picture", data, content_type)}
        )
        url = answer.get("secure_url")
        expected = f"https://res.cloudinary.com/{self.cloud}/"
        if not isinstance(url, str) or not url.startswith(expected):
            raise ServiceError(502, "the picture could not be stored. Try again in a moment")
        # The address is the same from one upload to the next within a second, and a
        # browser would go on showing the old picture. This makes every upload's unique.
        return f"{url}?u={secrets.token_hex(4)}"

    async def delete(
        self, account_id: uuid.UUID, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Delete the account's picture. One that is already gone is not an error."""
        fields = self._signed({"public_id": self.public_id(account_id), "invalidate": "true"})
        await self._post("destroy", fields, transport)

    async def _post(
        self,
        action: str,
        fields: dict[str, str],
        transport: httpx.AsyncBaseTransport | None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, object]:
        address = f"https://api.cloudinary.com/v1_1/{self.cloud}/image/{action}"
        try:
            async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
                answer = await client.post(address, data=fields, files=files)
            if answer.status_code != 200:
                raise ServiceError(502, "the picture could not be stored. Try again in a moment")
            body = answer.json()
        except (httpx.HTTPError, ValueError):
            raise ServiceError(502, "the picture service could not be reached") from None
        return body if isinstance(body, dict) else {}
