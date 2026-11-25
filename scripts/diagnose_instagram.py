"""Check which Instagram request styles work from this machine / proxy.

Run:  make diagnose            (uses the first proxy from .env, if any)
      make diagnose HANDLE=nasa  (test a different profile)

Prints only status codes and short hints. Never prints passwords or proxy credentials.
"""

import asyncio
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import Settings

MOBILE_UA = (
    "Instagram 219.0.0.12.117 Android (31/12; 420dpi; 1080x2400; samsung; SM-G991B; "
    "o1s; exynos2100; en_US; 346138365)"
)


def tests(s: Settings, user: str) -> list[tuple[str, str, dict[str, str]]]:
    api = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={user}"
    return [
        (
            "A  web API, our current headers",
            api,
            {
                "User-Agent": s.user_agent,
                "X-IG-App-ID": s.ig_app_id,
                "X-ASBD-ID": s.ig_asbd_id,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"https://www.instagram.com/{user}/",
                "Origin": "https://www.instagram.com",
                "Cookie": "csrftoken=abcdefghijklmnopqrstuvwxyz123456; ig_nrcb=1",
                "Accept": "*/*",
            },
        ),
        (
            "B  web API, minimal headers",
            api,
            {"User-Agent": s.user_agent, "X-IG-App-ID": s.ig_app_id},
        ),
        (
            "C  mobile API (i.instagram.com)",
            f"https://i.instagram.com/api/v1/users/web_profile_info/?username={user}",
            {"User-Agent": MOBILE_UA, "X-IG-App-ID": "567067343352427"},
        ),
        (
            "D  profile web page",
            f"https://www.instagram.com/{user}/",
            {"User-Agent": s.user_agent, "Accept": "text/html"},
        ),
        (
            "E  login page",
            "https://www.instagram.com/accounts/login/",
            {"User-Agent": s.user_agent, "Accept": "text/html"},
        ),
    ]


def describe(resp: httpx.Response) -> str:
    ctype = resp.headers.get("content-type", "").split(";")[0]
    if "json" in ctype:
        try:
            body = resp.json()
        except ValueError:
            return "json (unreadable)"
        if isinstance(body, dict) and (body.get("data") or {}).get("user"):
            user = body["data"]["user"]
            followers = (user.get("edge_followed_by") or {}).get("count")
            return f"WORKS: got profile data (followers={followers})"
        return f"json, message={str(body.get('message', ''))[:70]!r}"
    if "html" in ctype:
        title = re.search(r"<title>\s*(.*?)\s*</title>", resp.text, re.S)
        og = re.search(r'og:description" content="([^"]{0,80})', resp.text)
        hint = f"title={title.group(1).strip()[:45]!r}" if title else "no title"
        return hint + (f", meta={og.group(1)!r}" if og else ", no profile meta")
    return ctype or "empty"


async def main() -> None:
    user = sys.argv[1] if len(sys.argv) > 1 else "natgeo"
    s = Settings()
    proxy = s.proxies[0] if s.proxies else s.proxy_url
    print(
        f"Testing @{user} via {'a proxy from .env' if proxy else 'your own internet (no proxy)'}\n"
    )
    async with httpx.AsyncClient(
        proxy=proxy, timeout=20, http2=True, follow_redirects=False
    ) as client:
        for name, url, headers in tests(s, user):
            try:
                resp = await client.get(url, headers=headers)
                where = f" -> {resp.headers.get('location', '')[:60]}" if resp.is_redirect else ""
                print(f"{name:34} {resp.status_code}{where}  {describe(resp)}")
            except httpx.HTTPError as exc:
                print(f"{name:34} ERROR {type(exc).__name__}: {str(exc)[:80]}")
    print("\nCopy everything above and send it to Claude.")


if __name__ == "__main__":
    asyncio.run(main())
