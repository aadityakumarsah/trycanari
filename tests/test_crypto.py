from __future__ import annotations

import base64

import pytest

from agentbounty.services.crypto import Cipher, DecryptionError, generate_key


def test_round_trip_and_fresh_ciphertext_each_time() -> None:
    cipher = Cipher(generate_key())
    first = cipher.encrypt(b"the secret prompt", purpose="prompt")
    second = cipher.encrypt(b"the secret prompt", purpose="prompt")
    assert first != second
    assert b"secret" not in first
    assert cipher.decrypt(first, purpose="prompt") == b"the secret prompt"
    assert cipher.decrypt(second, purpose="prompt") == b"the secret prompt"


def test_json_round_trip() -> None:
    cipher = Cipher(generate_key())
    value = [{"name": "transfer_funds", "parameters": {"type": "object"}, "n": 3}]
    assert (
        cipher.decrypt_json(cipher.encrypt_json(value, purpose="tools"), purpose="tools") == value
    )


def test_ciphertext_is_bound_to_its_key_its_purpose_and_its_bytes() -> None:
    cipher = Cipher(generate_key())
    ciphertext = cipher.encrypt(b"TREASURY-KEY", purpose="canaries.secret")
    with pytest.raises(DecryptionError, match="authentication"):
        Cipher(generate_key()).decrypt(ciphertext, purpose="canaries.secret")
    with pytest.raises(DecryptionError, match="authentication"):
        cipher.decrypt(ciphertext, purpose="target_configs.system_prompt")
    damaged = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    with pytest.raises(DecryptionError, match="authentication"):
        cipher.decrypt(damaged, purpose="canaries.secret")
    for unrecognised in (b"", b"\x02" + ciphertext[1:], b"\x01short"):
        with pytest.raises(DecryptionError, match="unrecognised"):
            cipher.decrypt(unrecognised, purpose="canaries.secret")


@pytest.mark.parametrize("key", ["", "not base64!", base64.b64encode(b"k" * 16).decode()])
def test_keys_must_be_32_bytes(key: str) -> None:
    with pytest.raises(ValueError, match=r"32 bytes|base64|padding|Incorrect"):
        Cipher(key)


def test_generated_keys_are_valid_and_distinct() -> None:
    first, second = generate_key(), generate_key()
    assert first != second
    assert len(base64.b64decode(first)) == 32
    Cipher(first)
