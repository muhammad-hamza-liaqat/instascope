import pytest

from app.core.errors import InvalidUsername
from app.core.utils import normalize_username
from app.schemas.instagram import MediaType
from app.scraper.parsers import (
    parse_clips_page,
    parse_feed_page,
    parse_graph_post,
    parse_profile,
    timeline_has_more,
    timeline_nodes,
)
from tests.conftest import load


def _user():
    return load("web_profile_info.json")["data"]["user"]


def test_parse_profile_fields():
    p = parse_profile(_user())
    assert p.id == "787132"
    assert p.username == "natgeo"
    assert p.followers == 283_000_000
    assert p.following == 150
    assert p.posts_count == 30000
    assert p.is_verified and p.is_business
    assert p.bio_links[0].url == "https://natgeo.com/shop"
    assert p.profile_pic_url_hd == "https://cdn.example.com/pic_hd.jpg"
    assert p.url == "https://www.instagram.com/natgeo/"


def test_parse_graph_posts_types_and_counts():
    user = _user()
    posts = [parse_graph_post(n) for n in timeline_nodes(user)]
    image, reel, carousel = posts
    assert timeline_has_more(user)

    assert image.media_type == MediaType.IMAGE
    assert image.hashtags == ["mountains", "travel"]
    assert image.mentions == ["explorer"]
    assert image.location == "Swiss Alps"
    assert image.url == "https://www.instagram.com/p/AAA111/"

    assert reel.media_type == MediaType.REEL and reel.is_reel
    assert reel.likes == 0  # a real 0 must not fall through to the preview count
    assert reel.views == 900000
    assert reel.audio == "Ocean Hymn - Blue Choir"
    assert reel.url == "https://www.instagram.com/reel/BBB222/"

    assert carousel.media_type == MediaType.CAROUSEL
    assert len(carousel.children) == 2 and carousel.children[1].is_video
    assert carousel.caption is None


def test_parse_clips_page():
    items, cursor, more = parse_clips_page(load("clips_user.json"))
    assert [i.shortcode for i in items] == ["BBB222", "DDD444"]
    assert items[0].plays == 2_500_000
    assert items[0].audio == "Ocean Hymn - Blue Choir"
    assert items[1].audio == "Original audio"
    assert items[1].caption is None
    assert cursor == "NEXT" and more is False


def test_parse_feed_page_carousel():
    items, cursor, more = parse_feed_page(load("feed_user.json"))
    assert items[1].media_type == MediaType.CAROUSEL
    assert items[1].display_url == "https://cdn.example.com/5a.jpg"
    assert items[1].children[1].video_url == "https://cdn.example.com/5b.mp4"
    assert cursor is None and more is False


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("natgeo", "natgeo"),
        ("@NatGeo", "natgeo"),
        ("https://www.instagram.com/natgeo/?hl=en", "natgeo"),
        ("  nat.geo_1 ", "nat.geo_1"),
    ],
)
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


@pytest.mark.parametrize("raw", ["", "bad name", "x" * 31, "emoji🙂"])
def test_normalize_username_rejects(raw):
    with pytest.raises(InvalidUsername):
        normalize_username(raw)
