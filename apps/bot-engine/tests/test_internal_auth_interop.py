"""Cross-language: Node signs, Python verifies."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from app.api.auth import HEADER_SIG, HEADER_TS, HEADER_USER, InternalAuthenticator

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_node_signs_python_verifies() -> None:
    if not (REPO_ROOT / "apps/gateway/node_modules").is_dir():
        pytest.skip("gateway node_modules not installed; run `pnpm install` first")

    secret = "shared-internal-secret-xyz"
    body = '{"strategy_type":"dca","quote_amount":"100"}'
    ts = int(time.time())
    script = (
        "import('./src/clients/internal-auth.ts').then(m => {"
        "  const s = new m.InternalAuthSigner(process.env.SECRET);"
        "  const headers = s.sign({"
        "    method: process.env.METHOD,"
        "    path: process.env.PATH_,"
        "    userId: process.env.USER_,"
        "    body: process.env.BODY,"
        "    ts: Number(process.env.TS),"
        "  });"
        "  process.stdout.write(JSON.stringify(headers));"
        "});"
    )
    result = subprocess.run(
        ["pnpm", "exec", "tsx", "-e", script],
        cwd=REPO_ROOT / "apps" / "gateway",
        env={
            **os.environ,
            "SECRET": secret,
            "METHOD": "POST",
            "PATH_": "/bots/b1/start",
            "USER_": "u-42",
            "BODY": body,
            "TS": str(ts),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Node signer failed: {result.stderr}"

    headers = json.loads(result.stdout)
    assert headers[HEADER_USER] == "u-42"
    assert headers[HEADER_TS] == str(ts)

    auth = InternalAuthenticator(secret.encode())
    ident = auth.verify(
        method="POST",
        path="/bots/b1/start",
        headers=headers,
        body=body.encode(),
        now=ts,
    )
    assert ident.user_id == "u-42"
