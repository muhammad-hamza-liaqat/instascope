"""Check which Instagram request styles work from this machine / proxy.

Run:  make diagnose            (uses the first proxy from .env, if any)
      make diagnose HANDLE=nasa  (test a different profile)

Part 1 tests logged-out requests (through the first proxy). Part 2 tests logged-in requests
using the saved session of the first account in accounts.txt / IG_SESSION_IDS (no login is
performed). Prints only status codes and short hints, never passwords, sessions or proxy
credentials.
"""

import asyncio
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import Settings
from app.scraper.accounts import configured_accounts, session_user_id
from app.scraper.proxies import ProxyRouter

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
        if isinstance(body, dict) and isinstance(body.get("user"), dict):
            user = body["user"]
            return (
                f"WORKS: user object (followers={user.get('follower_count')}, pk={user.get('pk')})"
            )
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            first = body["items"][0] if body["items"] else {}
            owner = (first.get("user") or {}) if isinstance(first, dict) else {}
            return f"WORKS: {len(body['items'])} items (owner={owner.get('username')}, pk={owner.get('pk')})"
        if isinstance(body, dict) and isinstance(body.get("users"), list):
            pks = [(u.get("user") or {}).get("pk") for u in body["users"][:1]]
            return f"WORKS: search results ({len(body['users'])}), first pk={pks}"
        return f"json, message={str(body.get('message', ''))[:70]!r}"
    if "html" in ctype:
        title = re.search(r"<title>\s*(.*?)\s*</title>", resp.text, re.S)
        og = re.search(r'og:description" content="([^"]{0,80})', resp.text)
        hint = f"title={title.group(1).strip()[:45]!r}" if title else "no title"
        embedded = [
            k for k in ("follower_count", "edge_followed_by", "biography") if k in resp.text
        ]
        return (
            hint
            + (f", meta={og.group(1)!r}" if og else ", no profile meta")
            + (f", page contains {embedded}" if embedded else "")
        )
    return ctype or "empty"


def logged_in_tests(s: Settings, user: str) -> list[tuple[str, str, dict[str, str]]]:
    base = "https://www.instagram.com"
    web = {
        "User-Agent": s.user_agent,
        "X-IG-App-ID": s.ig_app_id,
        "X-ASBD-ID": s.ig_asbd_id,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{base}/{user}/",
        "Accept": "*/*",
    }
    return [
        (
            "L1 web_profile_info (current)",
            f"{base}/api/v1/users/web_profile_info/?username={user}",
            web,
        ),
        (
            "L2 web_profile_info, mobile host",
            f"https://i.instagram.com/api/v1/users/web_profile_info/?username={user}",
            {"User-Agent": MOBILE_UA, "X-IG-App-ID": "567067343352427"},
        ),
        ("L3 feed by username", f"{base}/api/v1/feed/user/{user}/username/?count=12", web),
        ("L4 search", f"{base}/web/search/topsearch/?query={user}", web),
        ("L5 user info by id (natgeo=787132)", f"{base}/api/v1/users/787132/info/", web),
        (
            "L6 profile web page",
            f"{base}/{user}/",
            {"User-Agent": s.user_agent, "Accept": "text/html"},
        ),
    ]


def first_session(s: Settings) -> str | None:
    for _, _, seed in configured_accounts(s):
        if seed:
            return seed
    return None


async def run(
    client: httpx.AsyncClient, tests_: list[tuple[str, str, dict[str, str]]], cookie: str
) -> None:
    for name, url, headers in tests_:
        try:
            h = {**headers, "Cookie": cookie, "X-CSRFToken": "abcdefghijklmnopqrstuvwxyz123456"}
            resp = await client.get(url, headers=h)
            where = f" -> {resp.headers.get('location', '')[:60]}" if resp.is_redirect else ""
            print(f"{name:36} {resp.status_code}{where}  {describe(resp)}")
        except httpx.HTTPError as exc:
            print(f"{name:36} ERROR {type(exc).__name__}: {str(exc)[:80]}")


async def main() -> None:
    user = sys.argv[1] if len(sys.argv) > 1 else "natgeo"
    s = Settings()
    csrf = "csrftoken=abcdefghijklmnopqrstuvwxyz123456; ig_nrcb=1"

    proxy = s.proxies[0] if s.proxies else s.proxy_url
    print(f"PART 1: logged-out, @{user}, via {'a proxy' if proxy else 'your own internet'}\n")
    async with httpx.AsyncClient(proxy=proxy, timeout=20, http2=True) as client:
        await run(client, tests(s, user), csrf)

    session = first_session(s)
    print()
    if not session:
        print("PART 2 skipped: no saved session (add ' session=...' to an accounts.txt line).")
    else:
        account_proxy = ProxyRouter(s).for_account("diagnose") if s.accounts_use_proxy else None
        uid = session_user_id(session) or ""
        cookie = f"{csrf}; sessionid={session}; ds_user_id={uid}"
        where = "a proxy" if account_proxy else "your own internet"
        print(f"PART 2: LOGGED IN with the saved session, @{user}, via {where}\n")
        async with httpx.AsyncClient(proxy=account_proxy, timeout=20, http2=True) as client:
            await run(client, logged_in_tests(s, user), cookie)
    print("\nCopy everything above and send it to Claude.")


if __name__ == "__main__":
    asyncio.run(main())
