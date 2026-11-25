"""Instagram web-app GraphQL: the way instagram.com itself loads profiles (captured 2026-11-25).

Flow (needs a logged-in account):
  1. GET https://www.instagram.com/{username}/  -> page tokens (lsd, fb_dtsg, viewer id) + profile id
  2. POST /api/graphql  PolarisProfilePageContentQuery          (id)        -> profile fields
  3. POST /api/graphql  PolarisProfilePostsTabContentQuery_connection (username, cursor) -> posts
  4. POST /api/graphql  PolarisProfileReelsTabContentQuery      (user id)   -> reels

Query ids (doc_id) change when Instagram ships a new web build; they live in settings
(IG_DOC_PROFILE / IG_DOC_POSTS / IG_DOC_REELS) so they can be updated without a code change.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.schemas.instagram import MediaData
from app.scraper.parsers import parse_v1_media

PROFILE_QUERY = "PolarisProfilePageContentQuery"
POSTS_QUERY = "PolarisProfilePostsTabContentQuery_connection"
REELS_QUERY = "PolarisProfileReelsTabContentQuery"

_PV = "__relay_internal__pv__"


def profile_variables(user_id: str) -> dict[str, Any]:
    return {
        "enable_integrity_filters": True,
        "id": user_id,
        f"{_PV}PolarisCannesGuardianExperienceEnabledrelayprovider": True,
        f"{_PV}PolarisCASB976ProfileEnabledrelayprovider": False,
        f"{_PV}PolarisProfileInformTreatmentBannerEnabledrelayprovider": False,
        f"{_PV}PolarisWebSchoolsEnabledrelayprovider": False,
        f"{_PV}PolarisProfileRingsGradientEnabledrelayprovider": True,
        f"{_PV}PolarisDSA16EntrypointEnabledrelayprovider": False,
        f"{_PV}PolarisRepostsConsumptionEnabledrelayprovider": True,
        f"{_PV}PolarisShortDramaEnabledrelayprovider": True,
    }


def posts_variables(username: str, after: str | None, first: int) -> dict[str, Any]:
    return {
        "after": after,
        "before": None,
        "data": {
            "count": first,
            "include_reel_media_seen_timestamp": True,
            "include_relationship_info": True,
            "latest_besties_reel_media": True,
            "latest_reel_media": True,
        },
        "first": first,
        "include_multi_captions": True,
        "last": None,
        "username": username,
        f"{_PV}PolarisMultiCaptionCarouselEnabledrelayprovider": True,
        f"{_PV}PolarisShortDramaEnabledrelayprovider": True,
        f"{_PV}PolarisSEOIsCrawlerrelayprovider": False,
        f"{_PV}PolarisProductTagIndicatorEnabledrelayprovider": False,
        f"{_PV}PolarisThreadsStyleCommentsEnabledrelayprovider": False,
        f"{_PV}PolarisReelsRecoDebugOverlayEnabledrelayprovider": False,
    }


def reels_variables(user_id: str, after: str | None, page_size: int) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "data": {"include_feed_video": True, "page_size": page_size, "target_user_id": user_id},
        "user_id": user_id,
        f"{_PV}PolarisShortDramaEnabledrelayprovider": True,
    }
    if after:  # pagination arguments were not observed live; Relay convention
        variables["after"] = after
        variables["first"] = page_size
    return variables


# ------------------------------------------------------------------ tokens
@dataclass
class WebTokens:
    lsd: str | None = None
    fb_dtsg: str | None = None
    viewer_id: str | None = None  # `av` form field
    profile_id: str | None = None  # id of the profile whose page was loaded
    csrf: str | None = None  # the session's real csrftoken (page cookie or embedded config)
    app_id: str | None = None  # X-IG-App-ID the page itself uses
    page_params: dict[str, str] = field(default_factory=dict)  # __hs, __rev, __spin_*, ...

    @property
    def usable(self) -> bool:
        return bool(self.lsd or self.fb_dtsg)

    def summary(self) -> str:
        """Which tokens were found (yes/no only, never values)."""
        flags = {
            "lsd": self.lsd,
            "fb_dtsg": self.fb_dtsg,
            "viewer": self.viewer_id,
            "csrf": self.csrf,
            "app_id": self.app_id,
        }
        found = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in flags.items())
        return f"{found}, page params={sorted(self.page_params) or 'none'}"


_LSD = [re.compile(r'"LSD",\[\],\{"token":"([^"]+)"'), re.compile(r'"lsd":"([^"]+)"')]
_DTSG = [
    re.compile(r'"DTSGInitialData",\[\],\{"token":"([^"]+)"'),
    re.compile(r'"dtsg":\{"token":"([^"]+)"'),
    re.compile(r'name="fb_dtsg" value="([^"]+)"'),
]
_VIEWER = [
    re.compile(r'"actorID":"(\d+)"'),
    re.compile(r'"viewerID":"(\d+)"'),
    re.compile(r'"NON_FACEBOOK_USER_ID":"(\d+)"'),
]


def _first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


_CSRF = [
    re.compile(r'"csrf_token":"([^"]+)"'),
    re.compile(r'\\"csrf_token\\":\\"([^"\\]+)'),
]
_APP_ID = [re.compile(r'"X-IG-App-ID":"(\d+)"'), re.compile(r'"appId":"(\d+)"')]
_PAGE_PARAMS = {
    "__hs": re.compile(r'"haste_session":"([^"]+)"'),
    "__rev": re.compile(r'"client_revision":(\d+)'),
    "__hsi": re.compile(r'"hsi":"(\d+)"'),
    "__spin_r": re.compile(r'"__spin_r":(\d+)'),
    "__spin_b": re.compile(r'"__spin_b":"([^"]+)"'),
    "__spin_t": re.compile(r'"__spin_t":(\d+)'),
}


def parse_tokens(html: str, username: str, cookie_csrf: str | None = None) -> WebTokens:
    name = re.escape(username)
    profile_patterns = [
        re.compile(r'"profile_id":"(\d+)"'),
        re.compile(r'"profilePage_(\d+)"'),
        re.compile(r'"pk":"(\d+)","username":"' + name + '"'),
        re.compile(r'"id":"(\d+)","username":"' + name + '"'),
        re.compile(r'"username":"' + name + r'"[^{}]{0,200}?"(?:pk|id)":"(\d+)"'),
    ]
    return WebTokens(
        lsd=_first_match(_LSD, html),
        fb_dtsg=_first_match(_DTSG, html),
        viewer_id=_first_match(_VIEWER, html),
        profile_id=_first_match(profile_patterns, html),
        csrf=cookie_csrf or _first_match(_CSRF, html),
        app_id=_first_match(_APP_ID, html),
        page_params={
            key: match.group(1)
            for key, pattern in _PAGE_PARAMS.items()
            if (match := pattern.search(html))
        },
    )


def jazoest(token: str) -> str:
    return "2" + str(sum(ord(ch) for ch in token))


# --------------------------------------------------------------- responses
def loads_first(text: str) -> Any:
    """Parse a GraphQL reply: strips the `for (;;);` anti-hijacking prefix, and handles
    several JSON documents in one reply (one per line, from @defer)."""
    text = text.strip()
    if text.startswith("for (;;);"):
        text = text[len("for (;;);") :].lstrip()
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(text.split("\n", 1)[0])


def find_connection(obj: Any, depth: int = 0) -> dict[str, Any] | None:
    """Find the first Relay connection (a dict with an `edges` list) in a reply."""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get("edges"), list):
            return obj
        for value in obj.values():
            found = find_connection(value, depth + 1)
            if found is not None:
                return found
    return None


def parse_connection(payload: dict[str, Any]) -> tuple[list[MediaData], str | None, bool]:
    conn = find_connection(payload.get("data") or {}) or {}
    items: list[MediaData] = []
    for edge in conn.get("edges") or []:
        node = (edge or {}).get("node") or {}
        media = node.get("media") if isinstance(node.get("media"), dict) else node
        if isinstance(media, dict) and media.get("code"):
            items.append(parse_v1_media(media))
    page = conn.get("page_info") or {}
    return items, page.get("end_cursor"), bool(page.get("has_next_page"))


def owner_id(items: list[MediaData]) -> str | None:
    for item in items:
        user = (item.raw or {}).get("user") or {}
        if user.get("pk") or user.get("id"):
            return str(user.get("pk") or user.get("id"))
    return None
