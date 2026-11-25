"""Turn raw Instagram JSON into our models.

Two media shapes exist:
- GraphQL "node" objects (from `web_profile_info` timeline edges)
- v1 "media" objects (from feed and clips endpoints)
"""

import re
from datetime import UTC, datetime
from typing import Any

from app.schemas.instagram import BioLink, CarouselItem, MediaData, MediaType, ProfileData

_HASHTAG = re.compile(r"#(\w+)", re.UNICODE)
_MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9._]{1,30})")


# ------------------------------------------------------------------ helpers
def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _dig(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> datetime | None:
    stamp = _int(value)
    return datetime.fromtimestamp(stamp, tz=UTC) if stamp else None


def _media(**fields: Any) -> MediaData:
    caption = fields.get("caption") or ""
    fields["hashtags"] = list(dict.fromkeys(t.lower() for t in _HASHTAG.findall(caption)))
    fields["mentions"] = list(
        dict.fromkeys(m.rstrip(".").lower() for m in _MENTION.findall(caption))
    )
    return MediaData(**fields)


# ------------------------------------------------------------------ profile
def parse_profile(user: dict[str, Any]) -> ProfileData:
    links = [
        BioLink(title=link.get("title") or None, url=link["url"])
        for link in user.get("bio_links") or []
        if isinstance(link, dict) and link.get("url")
    ]
    return ProfileData(
        id=str(user["id"]),
        username=user["username"],
        full_name=user.get("full_name") or None,
        biography=user.get("biography") or None,
        bio_links=links,
        external_url=user.get("external_url") or None,
        followers=_int(_dig(user, "edge_followed_by", "count")) or 0,
        following=_int(_dig(user, "edge_follow", "count")) or 0,
        posts_count=_int(_dig(user, "edge_owner_to_timeline_media", "count")) or 0,
        highlight_count=_int(user.get("highlight_reel_count")),
        is_private=bool(user.get("is_private")),
        is_verified=bool(user.get("is_verified")),
        is_business=bool(user.get("is_business_account")),
        is_professional=bool(user.get("is_professional_account")),
        category=user.get("category_name") or user.get("business_category_name") or None,
        business_email=user.get("business_email") or None,
        business_phone=user.get("business_phone_number") or None,
        pronouns=list(user.get("pronouns") or []),
        profile_pic_url=user.get("profile_pic_url") or None,
        profile_pic_url_hd=user.get("profile_pic_url_hd") or user.get("profile_pic_url") or None,
        fbid=str(user["fbid"]) if user.get("fbid") else None,
    )


def parse_profile_v1(user: dict[str, Any]) -> ProfileData:
    """Profile from a v1-style user object (web GraphQL `PolarisProfilePageContentQuery`)."""
    links = [
        BioLink(title=link.get("title") or None, url=link["url"])
        for link in user.get("bio_links") or []
        if isinstance(link, dict) and link.get("url")
    ]
    hd = (user.get("hd_profile_pic_url_info") or {}).get("url")
    return ProfileData(
        id=str(user.get("pk") or user["id"]),
        username=user["username"],
        full_name=user.get("full_name") or None,
        biography=user.get("biography") or None,
        bio_links=links,
        external_url=user.get("external_url") or None,
        followers=_int(user.get("follower_count")) or 0,
        following=_int(user.get("following_count")) or 0,
        posts_count=_int(user.get("media_count")) or 0,
        highlight_count=_int(user.get("highlight_reel_count")),
        is_private=bool(user.get("is_private")),
        is_verified=bool(user.get("is_verified")),
        is_business=bool(user.get("is_business")),
        is_professional=bool(user.get("is_professional_account")),
        category=user.get("category") or user.get("category_name") or None,
        business_email=user.get("public_email") or user.get("business_email") or None,
        business_phone=user.get("public_phone_number") or user.get("contact_phone_number") or None,
        pronouns=list(user.get("pronouns") or []),
        profile_pic_url=user.get("profile_pic_url") or None,
        profile_pic_url_hd=hd or user.get("profile_pic_url") or None,
        fbid=str(user["fbid_v2"]) if user.get("fbid_v2") else None,
    )


def timeline_nodes(user: dict[str, Any]) -> list[dict[str, Any]]:
    edges = _dig(user, "edge_owner_to_timeline_media", "edges") or []
    return [edge["node"] for edge in edges if isinstance(edge, dict) and edge.get("node")]


def timeline_has_more(user: dict[str, Any]) -> bool:
    return bool(_dig(user, "edge_owner_to_timeline_media", "page_info", "has_next_page"))


# ----------------------------------------------------------- GraphQL nodes
def parse_graph_post(node: dict[str, Any], owner: str | None = None) -> MediaData:
    if node.get("product_type") == "clips":
        media_type = MediaType.REEL
    elif node.get("__typename") == "GraphSidecar" or "edge_sidecar_to_children" in node:
        media_type = MediaType.CAROUSEL
    elif node.get("is_video"):
        media_type = MediaType.VIDEO
    else:
        media_type = MediaType.IMAGE

    caption_edges = _dig(node, "edge_media_to_caption", "edges") or []
    children = [
        CarouselItem(
            display_url=edge["node"].get("display_url"),
            video_url=edge["node"].get("video_url"),
            is_video=bool(edge["node"].get("is_video")),
        )
        for edge in _dig(node, "edge_sidecar_to_children", "edges") or []
        if edge.get("node")
    ]
    music = node.get("clips_music_attribution_info") or {}
    audio = " - ".join(x for x in (music.get("song_name"), music.get("artist_name")) if x) or None

    return _media(
        id=str(node["id"]),
        shortcode=node["shortcode"],
        media_type=media_type,
        caption=_dig(caption_edges[0], "node", "text") if caption_edges else None,
        likes=_int(
            _first(
                _dig(node, "edge_liked_by", "count"), _dig(node, "edge_media_preview_like", "count")
            )
        ),
        comments=_int(
            _first(
                _dig(node, "edge_media_to_comment", "count"),
                _dig(node, "edge_media_preview_comment", "count"),
            )
        ),
        views=_int(node.get("video_view_count")),
        plays=_int(node.get("video_play_count")),
        duration=node.get("video_duration"),
        taken_at=_ts(node.get("taken_at_timestamp")),
        display_url=node.get("display_url") or node.get("thumbnail_src"),
        video_url=node.get("video_url"),
        location=_dig(node, "location", "name"),
        width=_int(_dig(node, "dimensions", "width")),
        height=_int(_dig(node, "dimensions", "height")),
        owner_username=_dig(node, "owner", "username") or owner,
        is_pinned=bool(node.get("pinned_for_users")),
        audio=audio,
        children=children,
        raw=node,
    )


# ------------------------------------------------------------- v1 media
def _best_image(media: dict[str, Any]) -> str | None:
    candidates = _dig(media, "image_versions2", "candidates") or []
    return candidates[0].get("url") if candidates else None


def _best_video(media: dict[str, Any]) -> str | None:
    versions = media.get("video_versions") or []
    return versions[0].get("url") if versions else None


def _v1_audio(media: dict[str, Any]) -> str | None:
    clips = media.get("clips_metadata") or {}
    asset = _dig(clips, "music_info", "music_asset_info")
    if asset:
        return " - ".join(x for x in (asset.get("title"), asset.get("display_artist")) if x) or None
    return _dig(clips, "original_sound_info", "original_audio_title")


def parse_v1_media(media: dict[str, Any]) -> MediaData:
    if media.get("product_type") == "clips":
        media_type = MediaType.REEL
    elif media.get("media_type") == 8:
        media_type = MediaType.CAROUSEL
    elif media.get("media_type") == 2:
        media_type = MediaType.VIDEO
    else:
        media_type = MediaType.IMAGE

    children = [
        CarouselItem(
            display_url=_best_image(child),
            video_url=_best_video(child),
            is_video=child.get("media_type") == 2,
        )
        for child in media.get("carousel_media") or []
    ]
    media_id = media.get("pk") or str(media["id"]).split("_", 1)[0]

    return _media(
        id=str(media_id),
        shortcode=media["code"],
        media_type=media_type,
        caption=_dig(media, "caption", "text"),
        likes=_int(media.get("like_count")),
        comments=_int(media.get("comment_count")),
        views=_int(_first(media.get("view_count"), media.get("video_view_count"))),
        plays=_int(_first(media.get("play_count"), media.get("ig_play_count"))),
        duration=media.get("video_duration"),
        taken_at=_ts(media.get("taken_at")),
        display_url=_best_image(media) or (children[0].display_url if children else None),
        video_url=_best_video(media),
        location=_dig(media, "location", "name"),
        width=_int(media.get("original_width")),
        height=_int(media.get("original_height")),
        owner_username=_dig(media, "user", "username"),
        is_pinned=bool(
            media.get("timeline_pinned_user_ids") or media.get("clips_tab_pinned_user_ids")
        ),
        audio=_v1_audio(media),
        children=children,
        raw=media,
    )


def parse_feed_page(payload: dict[str, Any]) -> tuple[list[MediaData], str | None, bool]:
    items = [parse_v1_media(m) for m in payload.get("items") or [] if isinstance(m, dict)]
    return items, payload.get("next_max_id"), bool(payload.get("more_available"))


def parse_clips_page(payload: dict[str, Any]) -> tuple[list[MediaData], str | None, bool]:
    items = [parse_v1_media(i["media"]) for i in payload.get("items") or [] if i.get("media")]
    paging = payload.get("paging_info") or {}
    return items, paging.get("max_id"), bool(paging.get("more_available"))
