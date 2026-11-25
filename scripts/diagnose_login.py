"""Show what Instagram's login pages offer (csrf token + password-encryption key).

Run:  make diagnose-login
Does NOT log in and never sends a password. Prints no secrets (the encryption key is public).
Uses the same network path as account logins (ACCOUNTS_USE_PROXY).
"""

import asyncio
import contextlib
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import Settings
from app.scraper import endpoints as ep
from app.scraper.login import _CSRF_HTML, _ENCRYPTION_ANCHOR, _cookie, find_key
from app.scraper.proxies import ProxyRouter


async def main() -> None:
    s = Settings()
    proxy = ProxyRouter(s).for_account("diagnose")
    print(f"Network: {'proxy' if proxy else 'your own internet'}\n")
    headers = {
        "User-Agent": s.user_agent,
        "Accept-Language": "en-US,en;q=0.9",
        "X-IG-App-ID": s.ig_app_id,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": ep.LOGIN_PAGE,
    }
    pages = [
        ("shared_data", ep.SHARED_DATA, headers),
        ("login page", ep.LOGIN_PAGE, {**headers, "Accept": "text/html"}),
        ("web shared_data", ep.WEB_SHARED_DATA, headers),
    ]
    async with httpx.AsyncClient(proxy=proxy, timeout=20, http2=True) as http:
        for name, url, h in pages:
            try:
                resp = await http.get(url, headers=h)
            except httpx.HTTPError as exc:
                print(f"{name:16} ERROR {type(exc).__name__}")
                continue
            ctype = resp.headers.get("content-type", "").split(";")[0]
            data = {}
            if "json" in ctype:
                with contextlib.suppress(ValueError):
                    parsed = resp.json()
                    data = parsed if isinstance(parsed, dict) else {}
            csrf_where = (
                "json"
                if (data.get("config") or {}).get("csrf_token")
                else "cookie"
                if _cookie(http.cookies, "csrftoken")
                else "html"
                if "json" not in ctype and _CSRF_HTML.search(resp.text)
                else "none"
            )
            key_id, pub, version, where = find_key(resp, data)
            anchors = len(_ENCRYPTION_ANCHOR.findall(resp.text)) if "json" not in ctype else 0
            all_key_ids = sorted(set(re.findall(r'key_id\\?"\s*:\s*\\?"?(\d+)', resp.text)))[:6]
            header_key = resp.headers.get("ig-set-password-encryption-web-key-id")
            print(f"{name:16} HTTP {resp.status_code} {ctype}")
            print(
                f"{'':16} csrf: {csrf_where} | key: {where} key_id={key_id} version={version} "
                f"pub={pub[:10] + '...' if pub else None}"
            )
            print(
                f"{'':16} header key_id={header_key} | 'encryption' blocks={anchors} "
                f"| all key_ids on page={all_key_ids}\n"
            )
    print("Copy everything above and send it to Claude.")


if __name__ == "__main__":
    asyncio.run(main())
