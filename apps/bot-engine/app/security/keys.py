"""Envelope decryption — mirrors apps/gateway/src/security/keys.ts (AES-256-GCM)."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_LEN = 32
IV_LEN = 12
TAG_LEN = 16
VERSION = 1


@dataclass(frozen=True)
class EncryptedEnvelope:
    v: int
    dek_iv: str
    dek_ct: str
    data_iv: str
    data_ct: str

    @staticmethod
    def from_dict(d: dict) -> "EncryptedEnvelope":
        return EncryptedEnvelope(
            v=int(d["v"]),
            dek_iv=str(d["dek_iv"]),
            dek_ct=str(d["dek_ct"]),
            data_iv=str(d["data_iv"]),
            data_ct=str(d["data_ct"]),
        )

    @staticmethod
    def parse(s: str) -> "EncryptedEnvelope":
        return EncryptedEnvelope.from_dict(json.loads(s))


class EnvelopeCipher:
    """Decrypts envelopes produced by Node's EnvelopeCipher.

    The Bot Engine is the only service that decrypts user API keys. Never log
    plaintext, never return it over any API, never persist it.
    """

    __slots__ = ("_kek",)

    def __init__(self, kek_base64: str) -> None:
        kek = base64.b64decode(kek_base64)
        if len(kek) != KEY_LEN:
            raise ValueError(f"KEK must be 32 bytes (got {len(kek)})")
        self._kek = kek

    @classmethod
    def from_env(cls, env_var: str = "XBT_KEK") -> "EnvelopeCipher":
        raw = os.environ.get(env_var)
        if not raw:
            raise RuntimeError(f"{env_var} env var not set")
        return cls(raw)

    def decrypt(self, env: EncryptedEnvelope) -> str:
        if env.v != VERSION:
            raise ValueError(f"Unsupported envelope version: {env.v}")

        dek = AESGCM(self._kek).decrypt(
            base64.b64decode(env.dek_iv),
            base64.b64decode(env.dek_ct),
            None,
        )
        plaintext_bytes = AESGCM(dek).decrypt(
            base64.b64decode(env.data_iv),
            base64.b64decode(env.data_ct),
            None,
        )
        return plaintext_bytes.decode("utf-8")

    def encrypt(self, plaintext: str) -> EncryptedEnvelope:
        """Provided for tests / parity. Production encrypt path lives in the Gateway."""
        dek = AESGCM.generate_key(bit_length=256)
        dek_iv = os.urandom(IV_LEN)
        dek_ct = AESGCM(self._kek).encrypt(dek_iv, dek, None)

        data_iv = os.urandom(IV_LEN)
        data_ct = AESGCM(dek).encrypt(data_iv, plaintext.encode("utf-8"), None)

        return EncryptedEnvelope(
            v=VERSION,
            dek_iv=base64.b64encode(dek_iv).decode(),
            dek_ct=base64.b64encode(dek_ct).decode(),
            data_iv=base64.b64encode(data_iv).decode(),
            data_ct=base64.b64encode(data_ct).decode(),
        )
