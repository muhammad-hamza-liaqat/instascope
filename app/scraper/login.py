"""Automatic Instagram web login for pool accounts.

Flow: bootstrap (csrftoken + password-encryption public key) -> POST login form with the
password encrypted like the browser does (`#PWD_INSTAGRAM_BROWSER:10:...`).
Checkpoints and 2FA are reported, never bypassed: a human approves them.

Unverified against live Instagram as of 2026-11-25 (see CLAUDE.md).
"""

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.public import PublicKey, SealedBox

from app.core.config import Settings
from app.scraper import endpoints as ep

log = logging.getLogger(__name__)


class LoginStatus(StrEnum):
    OK = "ok"
    BAD_CREDENTIALS = "bad_credentials"
    CHECKPOINT = "checkpoint"
    TWO_FACTOR = "two_factor"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


@dataclass
class LoginResult:
    status: LoginStatus
    session_id: str | None = None
    user_id: str | None = None
    message: str = ""


def encrypt_password(
    password: str, *, key_id: int, public_key_hex: str, version: str, timestamp: int | None = None
) -> str:
    """Instagram browser password encryption (AES-256-GCM key sealed with libsodium)."""
    ts = str(timestamp or int(time.time()))
    aes_key = os.urandom(32)
    sealed_key = SealedBox(PublicKey(bytes.fromhex(public_key_hex))).encrypt(aes_key)
    encrypted = AESGCM(aes_key).encrypt(bytes(12), password.encode(), ts.encode())
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    payload = (
        bytes([1, key_id & 0xFF])
        + len(sealed_key).to_bytes(2, "little")
        + sealed_key
        + tag
        + ciphertext
    )
    return f"#PWD_INSTAGRAM_BROWSER:{version}:{ts}:{base64.b64encode(payload).decode()}"


def _cookie(jar: httpx.Cookies, name: str) -> str | None:
    try:
        return jar.get(name)
    except httpx.CookieConflict:
        return next((c.value for c in jar.jar if c.name == name), None)


_CSRF_HTML = re.compile(r'"csrf_token"\s*:\s*"([^"]+)"')
_ENCRYPTION_HTML = re.compile(r'"encryption"\s*:\s*(\{[^{}]*\})')


def _key_from(resp: httpx.Response, data: dict[str, Any]) -> tuple[str | None, str | None, str]:
    enc = data.get("encryption") or {}
    if not enc and "html" in resp.headers.get("content-type", ""):
        match = _ENCRYPTION_HTML.search(resp.text)
        if match:
            try:
                enc = json.loads(match.group(1))
            except ValueError:
                enc = {}
    key_id = enc.get("key_id") or resp.headers.get("ig-set-password-encryption-web-key-id")
    pub_key = enc.get("public_key") or resp.headers.get("ig-set-password-encryption-web-pub-key")
    version = enc.get("version") or resp.headers.get("ig-set-password-encryption-web-key-version")
    return (
        str(key_id) if key_id else None,
        str(pub_key) if pub_key else None,
        str(version or "10"),
    )


async def _bootstrap(
    http: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[str, str | None, str | None, str] | LoginResult:
    """Return (csrf, key_id, public_key, version), or a LoginResult describing the failure."""
    sources = [
        (ep.SHARED_DATA, headers),
        (ep.LOGIN_PAGE, {**headers, "Accept": "text/html,application/xhtml+xml"}),
    ]
    csrf: str | None = None
    key_id: str | None = None
    pub_key: str | None = None
    version = "10"
    errors: list[str] = []
    for url, req_headers in sources:
        try:
            resp = await http.get(url, headers=req_headers)
        except httpx.HTTPError as exc:
            errors.append(f"{url}: {exc!r}")
            continue
        if resp.status_code == 429:
            return LoginResult(LoginStatus.RATE_LIMITED, message="rate limited during bootstrap")
        data: dict[str, Any] = {}
        if "json" in resp.headers.get("content-type", ""):
            try:
                parsed = resp.json()
                data = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                pass
        csrf = (
            csrf
            or (data.get("config") or {}).get("csrf_token")
            or _cookie(http.cookies, "csrftoken")
        )
        if not csrf and "html" in resp.headers.get("content-type", ""):
            match = _CSRF_HTML.search(resp.text)
            csrf = match.group(1) if match else None
        found = _key_from(resp, data)
        if found[0] and found[1]:
            key_id, pub_key, version = found
        if csrf and key_id and pub_key:
            break
        errors.append(f"{url}: HTTP {resp.status_code}")
    if not csrf:
        return LoginResult(
            LoginStatus.ERROR, message="could not obtain csrftoken (" + "; ".join(errors) + ")"
        )
    return csrf, key_id, pub_key, version


async def web_login(
    http: httpx.AsyncClient, username: str, password: str, settings: Settings
) -> LoginResult:
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-IG-App-ID": settings.ig_app_id,
        "X-ASBD-ID": settings.ig_asbd_id,
        "X-Requested-With": "XMLHttpRequest",
        "X-Instagram-AJAX": "1",
        "Origin": ep.BASE_URL,
        "Referer": ep.LOGIN_PAGE,
    }

    # 1. Bootstrap: csrftoken + password-encryption key. Try the JSON endpoint first, then the
    #    login page (cookie, response headers, or values embedded in the HTML).
    boot = await _bootstrap(http, headers)
    if isinstance(boot, LoginResult):
        return boot
    csrf, key_id, pub_key, version = boot
    http.cookies.set("csrftoken", csrf, domain=".instagram.com")

    if key_id and pub_key:
        enc_password = encrypt_password(
            password, key_id=int(key_id), public_key_hex=pub_key, version=version
        )
    else:
        log.info("No encryption key offered; using plain-text login format for %s", username)
        enc_password = f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}"

    # 2. Login.
    try:
        resp = await http.post(
            ep.LOGIN_AJAX,
            data={
                "username": username,
                "enc_password": enc_password,
                "queryParams": "{}",
                "optIntoOneTap": "false",
                "trustedDeviceRecords": "{}",
            },
            headers={**headers, "X-CSRFToken": csrf},
        )
    except httpx.HTTPError as exc:
        return LoginResult(LoginStatus.ERROR, message=f"login request failed: {exc!r}")

    if resp.status_code == 429:
        return LoginResult(LoginStatus.RATE_LIMITED, message="rate limited during login")
    body: dict[str, Any] = {}
    try:
        parsed = resp.json()
        body = parsed if isinstance(parsed, dict) else {}
    except ValueError:
        pass
    message = str(body.get("message") or "")

    if body.get("authenticated"):
        session_id = _cookie(resp.cookies, "sessionid") or _cookie(http.cookies, "sessionid")
        if not session_id:
            return LoginResult(LoginStatus.ERROR, message="authenticated but no sessionid cookie")
        user_id = body.get("userId") or _cookie(resp.cookies, "ds_user_id")
        return LoginResult(
            LoginStatus.OK, session_id=session_id, user_id=str(user_id or "") or None
        )
    if body.get("two_factor_required"):
        return LoginResult(LoginStatus.TWO_FACTOR, message="two-factor authentication required")
    if body.get("checkpoint_url") or "checkpoint" in message or "challenge" in message:
        return LoginResult(LoginStatus.CHECKPOINT, message=message or "checkpoint required")
    if "wait a few minutes" in message.lower():
        return LoginResult(LoginStatus.RATE_LIMITED, message=message)
    if body.get("authenticated") is False:
        reason = "wrong password" if body.get("user") else "username not found"
        return LoginResult(LoginStatus.BAD_CREDENTIALS, message=reason)
    return LoginResult(
        LoginStatus.ERROR,
        message=f"unexpected login response {resp.status_code}: {message or resp.text[:200]}",
    )
