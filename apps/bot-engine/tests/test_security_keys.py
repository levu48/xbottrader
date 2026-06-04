"""Tests for envelope decryption + Node interop."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from app.security.keys import EncryptedEnvelope, EnvelopeCipher


def _kek() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_python_roundtrip() -> None:
    c = EnvelopeCipher(_kek())
    pt = "binance-api-key-7f2e9c1a4b8d"
    assert c.decrypt(c.encrypt(pt)) == pt


def test_python_roundtrip_empty() -> None:
    c = EnvelopeCipher(_kek())
    assert c.decrypt(c.encrypt("")) == ""


def test_python_roundtrip_large() -> None:
    c = EnvelopeCipher(_kek())
    pt = os.urandom(5000).hex()
    assert c.decrypt(c.encrypt(pt)) == pt


def test_different_ciphertexts_for_same_plaintext() -> None:
    c = EnvelopeCipher(_kek())
    a = c.encrypt("same")
    b = c.encrypt("same")
    assert a.data_ct != b.data_ct
    assert a.dek_ct != b.dek_ct


def test_wrong_kek_raises() -> None:
    c1 = EnvelopeCipher(_kek())
    c2 = EnvelopeCipher(_kek())
    env = c1.encrypt("secret")
    with pytest.raises(InvalidTag):
        c2.decrypt(env)


def test_tampered_data_ct_raises() -> None:
    c = EnvelopeCipher(_kek())
    env = c.encrypt("secret")
    raw = bytearray(base64.b64decode(env.data_ct))
    raw[0] ^= 0x01
    tampered = EncryptedEnvelope(
        v=env.v,
        dek_iv=env.dek_iv,
        dek_ct=env.dek_ct,
        data_iv=env.data_iv,
        data_ct=base64.b64encode(bytes(raw)).decode(),
    )
    with pytest.raises(InvalidTag):
        c.decrypt(tampered)


def test_tampered_dek_ct_raises() -> None:
    c = EnvelopeCipher(_kek())
    env = c.encrypt("secret")
    raw = bytearray(base64.b64decode(env.dek_ct))
    raw[0] ^= 0x01
    tampered = EncryptedEnvelope(
        v=env.v,
        dek_iv=env.dek_iv,
        dek_ct=base64.b64encode(bytes(raw)).decode(),
        data_iv=env.data_iv,
        data_ct=env.data_ct,
    )
    with pytest.raises(InvalidTag):
        c.decrypt(tampered)


def test_wrong_version_raises() -> None:
    c = EnvelopeCipher(_kek())
    env = c.encrypt("secret")
    bad = EncryptedEnvelope(
        v=999, dek_iv=env.dek_iv, dek_ct=env.dek_ct, data_iv=env.data_iv, data_ct=env.data_ct
    )
    with pytest.raises(ValueError, match="version"):
        c.decrypt(bad)


def test_bad_kek_length_raises() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        EnvelopeCipher(base64.b64encode(b"\x00" * 16).decode())


# ---- Cross-language interop: Node encrypts, Python decrypts ----

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_node_to_python_interop() -> None:
    """Generate a ciphertext in Node via a one-shot script, decrypt it in Python."""
    if not (REPO_ROOT / "apps/gateway/node_modules").is_dir():
        pytest.skip("gateway node_modules not installed; run `pnpm install` first")

    kek_b64 = base64.b64encode(os.urandom(32)).decode()
    plaintext = "cross-lang-secret-🔐"
    script = (
        "import('./src/security/keys.ts').then(m => {"
        "  const c = new m.EnvelopeCipher(process.env.KEK);"
        "  process.stdout.write(JSON.stringify(c.encrypt(process.env.PT)));"
        "});"
    )
    result = subprocess.run(
        ["pnpm", "exec", "tsx", "-e", script],
        cwd=REPO_ROOT / "apps" / "gateway",
        env={**os.environ, "KEK": kek_b64, "PT": plaintext},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Node script failed: {result.stderr}"
    env = EncryptedEnvelope.from_dict(json.loads(result.stdout))
    decrypted = EnvelopeCipher(kek_b64).decrypt(env)
    assert decrypted == plaintext
