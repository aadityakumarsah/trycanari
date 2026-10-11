"""Uploaded profile pictures, with Cloudinary replaced by a stand-in."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from agentbounty.services import pictures
from agentbounty.settings import Settings

from .platform import Platform, platform_on
from .platform import Sponsor as Account

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32
SECRET = "the-api-secret"


class Cloud:
    """Cloudinary: it keeps what is uploaded, by public ID, and checks every signature."""

    def __init__(self) -> None:
        self.stored: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.version = 0
        self.broken = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        action = request.url.path.rsplit("/", 1)[-1]
        assert request.url.path == f"/v1_1/democloud/image/{action}"
        body = request.read()
        if action == "upload":
            fields, picture = self._multipart(request, body)
        else:
            fields, picture = {k: v[0] for k, v in parse_qs(body.decode()).items()}, b""
        self.calls.append((action, fields))
        signed = {k: v for k, v in fields.items() if k not in ("api_key", "signature")}
        joined = "&".join(f"{key}={signed[key]}" for key in sorted(signed))
        expected = hashlib.sha1((joined + SECRET).encode(), usedforsecurity=False).hexdigest()
        if fields["signature"] != expected or fields["api_key"] != "12345":
            return httpx.Response(401, json={"error": {"message": "Invalid Signature"}})
        if self.broken:
            return httpx.Response(500, json={})
        if action == "destroy":
            found = self.stored.pop(fields["public_id"], None)
            return httpx.Response(200, json={"result": "ok" if found else "not found"})
        self.stored[fields["public_id"]] = picture
        self.version += 1
        address = f"https://res.cloudinary.com/democloud/image/upload/v{self.version}"
        return httpx.Response(200, json={"secure_url": f"{address}/{fields['public_id']}.jpg"})

    @staticmethod
    def _multipart(request: httpx.Request, body: bytes) -> tuple[dict[str, str], bytes]:
        boundary = request.headers["content-type"].split("boundary=")[1].encode()
        fields: dict[str, str] = {}
        picture = b""
        for part in body.split(b"--" + boundary)[1:-1]:
            head, _, value = part.strip(b"\r\n").partition(b"\r\n\r\n")
            name = head.split(b'name="')[1].split(b'"')[0].decode()
            if name == "file":
                picture = value
            else:
                fields[name] = value.decode()
        return fields, picture


@pytest.fixture
async def site(service_settings: Settings) -> AsyncIterator[tuple[Platform, Cloud]]:
    settings = service_settings.model_copy(
        update={"cloudinary_url": SecretStr(f"cloudinary://12345:{SECRET}@democloud")}
    )
    async with platform_on(settings) as platform:
        cloud = Cloud()
        platform.app.state.cloudinary_transport = httpx.MockTransport(cloud.handle)
        yield platform, cloud


async def put(account: Account, data: bytes, kind: str = "image/png") -> httpx.Response:
    return await account.client.put(
        "/v1/account/picture", content=data, headers={**account.headers, "Content-Type": kind}
    )


def test_the_kind_of_image_is_read_from_its_bytes() -> None:
    assert pictures.image_type(PNG) == "image/png"
    assert pictures.image_type(JPEG) == "image/jpeg"
    assert pictures.image_type(WEBP) == "image/webp"
    for not_one in (b"", b"GIF89a", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"RIFF....WAVE"):
        assert pictures.image_type(not_one) is None


async def test_a_new_picture_replaces_the_old_one_so_only_one_is_ever_stored(
    site: tuple[Platform, Cloud],
) -> None:
    platform, cloud = site
    ada = await platform.sponsor("ada@example.com")
    before = await ada.call("GET", "/v1/account")
    assert (before["picture_url"], before["picture_uploaded"], before["can_upload_picture"]) == (
        None,
        False,
        True,
    )

    first = await put(ada, PNG)
    assert first.status_code == 200
    one = first.json()["picture_url"]
    assert one.startswith("https://res.cloudinary.com/democloud/image/upload/v1/trycanari/avatars/")
    assert first.json()["picture_uploaded"] is True
    assert list(cloud.stored.values()) == [PNG]

    second = await put(ada, JPEG, "image/jpeg")
    two = second.json()["picture_url"]
    # A new address, so no browser shows the old picture, but the same place in storage:
    # the old picture was overwritten, not left beside the new one.
    assert two != one
    assert two.split("/v2/")[1].split("?")[0] == one.split("/v1/")[1].split("?")[0]
    assert list(cloud.stored.values()) == [JPEG]
    (_, sent) = cloud.calls[-1]
    assert (sent["overwrite"], sent["invalidate"], sent["format"]) == ("true", "true", "jpg")
    assert sent["transformation"] == pictures.STORED_AS

    # Someone else's picture is their own.
    bob = await platform.sponsor("bob@example.com")
    await put(bob, WEBP, "image/webp")
    assert len(cloud.stored) == 2

    # Removing it deletes it from storage too, and doing so twice is harmless.
    removed = await ada.call("DELETE", "/v1/account/picture")
    assert (removed["picture_url"], removed["picture_uploaded"]) == (None, False)
    assert list(cloud.stored.values()) == [WEBP]
    assert cloud.calls[-1][0] == "destroy"
    calls = len(cloud.calls)
    await ada.call("DELETE", "/v1/account/picture")
    assert len(cloud.calls) == calls


async def test_only_real_images_of_a_sensible_size_are_taken(site: tuple[Platform, Cloud]) -> None:
    platform, cloud = site
    ada = await platform.sponsor()
    # Called an image, but it is not one: the bytes decide, not the label.
    for fake in (
        b"<svg onload=alert(1)>",
        b"GIF89a" + b"\x00" * 20,
        b"MZ\x90\x00 an executable",
        b"",
    ):
        refused = await put(ada, fake, "image/png")
        assert (refused.status_code, refused.json()["detail"]) == (
            415,
            "upload a PNG, JPEG or WebP image",
        )
    huge = await put(ada, PNG + b"\x00" * pictures.MAX_BYTES)
    assert (huge.status_code, huge.json()["detail"]) == (413, "that picture is larger than 4 MB")
    assert cloud.calls == []
    assert (await platform.client.put("/v1/account/picture", content=PNG)).status_code == 401

    # Storage failing is said plainly, and the account is left as it was.
    cloud.broken = True
    failed = await put(ada, PNG)
    assert failed.status_code == 502
    assert (await ada.call("GET", "/v1/account"))["picture_url"] is None


async def test_uploading_is_off_unless_configured(platform: Platform) -> None:
    ada = await platform.sponsor()
    assert (await ada.call("GET", "/v1/account"))["can_upload_picture"] is False
    assert (await put(ada, PNG)).status_code == 404


def test_the_cloudinary_address_is_checked_and_kept_out_of_sight() -> None:
    base = {
        "app_env": "development",
        "database_url": "postgresql+asyncpg://u@localhost/d",
        "secret_encryption_key": "A" * 43 + "=",
        "model_backend": "fake",
    }
    good = Settings(**base, cloudinary_url=f"cloudinary://12345:{SECRET}@democloud")  # type: ignore[arg-type]
    assert SECRET not in repr(good)
    assert good.cloudinary_url is not None
    store = pictures.Cloudinary.from_url(good.cloudinary_url.get_secret_value())
    assert (store.cloud, store.api_key, store.api_secret) == ("democloud", "12345", SECRET)
    for bad in ("https://example.com", "cloudinary://democloud", "cloudinary://key@cloud"):
        with pytest.raises(ValidationError, match="cloudinary://"):
            Settings(**base, cloudinary_url=bad)  # type: ignore[arg-type]
