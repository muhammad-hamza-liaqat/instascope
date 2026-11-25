import copy
from pathlib import Path

import httpx

from app.scraper import endpoints as ep
from tests.conftest import load, mock_clips, mock_profile


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/health/ready").json()
    assert ready["database"] == "ok"


def test_profile_live_then_cache_then_refresh(client, ig):
    profile_route = mock_profile(ig)
    mock_clips(ig)

    first = client.get("/api/v1/profiles/@NatGeo")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["source"] == "live"
    assert body["profile"]["followers"] == 283_000_000
    assert body["profile"]["biography"].startswith("Inspiring")
    assert [p["shortcode"] for p in body["posts"]] == ["AAA111", "BBB222", "CCC333"]
    assert [r["shortcode"] for r in body["reels"]] == ["BBB222", "DDD444"]
    assert body["reels"][0]["plays"] == 2_500_000
    assert any("IG_SESSION_IDS" in w for w in body["warnings"])  # grid has more, no session

    second = client.get("/api/v1/profiles/natgeo").json()
    assert second["source"] == "cache"
    assert profile_route.call_count == 1
    assert len(second["posts"]) == 3
    assert {r["shortcode"] for r in second["reels"]} == {"BBB222", "DDD444"}

    third = client.get("/api/v1/profiles/natgeo", params={"refresh": True}).json()
    assert third["source"] == "live"
    assert profile_route.call_count == 2

    history = client.get("/api/v1/profiles/natgeo/history").json()
    assert len(history["points"]) == 2


def test_reels_endpoint_needs_its_own_freshness(client, ig):
    mock_profile(ig)
    clips = mock_clips(ig)
    posts = client.get("/api/v1/profiles/natgeo/posts", params={"limit": 2}).json()
    assert posts["count"] == 2 and clips.call_count == 0

    reels = client.get("/api/v1/profiles/natgeo/reels").json()
    assert reels["source"] == "live"  # reels were never scraped, so cache is not used
    assert reels["count"] == 2


def test_not_found_json_404(client, ig):
    mock_profile(ig, "ghost", payload={"message": "", "status": "fail"}, status=404)
    resp = client.get("/api/v1/profiles/ghost")
    assert resp.status_code == 404
    assert resp.json()["error"] == "profile_not_found"


def test_not_found_null_user(client, ig):
    mock_profile(ig, "ghost", payload={"data": {"user": None}, "status": "ok"})
    assert client.get("/api/v1/profiles/ghost").status_code == 404


def test_html_404_is_reported_as_blocked(client, ig):
    ig.get(ep.WEB_PROFILE_INFO).respond(404, html="<html>Page Not Found</html>")
    resp = client.get("/api/v1/profiles/natgeo")
    assert resp.status_code == 503
    assert resp.json()["error"] == "blocked"


def test_rate_limit_retries_then_429(client, ig):
    route = ig.get(ep.WEB_PROFILE_INFO).respond(429, json={"message": "", "status": "fail"})
    resp = client.get("/api/v1/profiles/natgeo")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    assert route.call_count == 2  # max_retries=1 in tests


def test_network_error_retries_then_502(client, ig):
    route = ig.get(ep.WEB_PROFILE_INFO).mock(side_effect=httpx.ConnectError("boom"))
    resp = client.get("/api/v1/profiles/natgeo")
    assert resp.status_code == 502
    assert route.call_count == 2


def test_private_profile(client, ig):
    payload = load("web_profile_info.json")
    payload["data"]["user"]["is_private"] = True
    mock_profile(ig, payload=payload)
    clips = mock_clips(ig)
    body = client.get("/api/v1/profiles/natgeo").json()
    assert body["posts"] == [] and body["reels"] == []
    assert any("private" in w for w in body["warnings"])
    assert clips.call_count == 0


def test_reels_fall_back_to_grid_when_login_required(client, ig):
    mock_profile(ig)
    mock_clips(ig, status=401, payload={"message": "login_required", "status": "fail"})
    body = client.get("/api/v1/profiles/natgeo").json()
    assert [r["shortcode"] for r in body["reels"]] == ["BBB222"]
    assert any("Reels tab unavailable" in w for w in body["warnings"])


def test_session_enables_post_pagination(app_factory, ig):
    client = app_factory(ig_session_ids=["123456%3Aabcdef"])
    mock_profile(ig)
    mock_clips(ig)
    feed = ig.get(ep.USER_FEED.format(user_id="787132")).respond(200, json=load("feed_user.json"))

    body = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 50}).json()
    assert [p["shortcode"] for p in body["posts"]] == ["AAA111", "BBB222", "CCC333", "EEE555"]
    assert body["posts"][0]["likes"] == 120500  # richer v1 payload wins
    cookie = feed.calls.last.request.headers["cookie"]
    assert "sessionid=123456%3Aabcdef" in cookie and "ds_user_id=123456" in cookie
    assert not any("IG_SESSION_IDS" in w for w in body["warnings"])


def test_logged_out_refusal_escalates_to_session(app_factory, ig):
    client = app_factory(ig_session_ids=["999%3Axyz"])
    route = ig.get(ep.WEB_PROFILE_INFO).mock(
        side_effect=[
            httpx.Response(401, json={"message": "login_required", "status": "fail"}),
            httpx.Response(200, json=load("web_profile_info.json")),
        ]
    )
    mock_clips(ig)
    ig.get(ep.USER_FEED.format(user_id="787132")).respond(200, json=load("feed_user.json"))
    assert client.get("/api/v1/profiles/natgeo").status_code == 200
    assert "sessionid" not in route.calls[0].request.headers["cookie"]
    assert "sessionid=999%3Axyz" in route.calls[1].request.headers["cookie"]


def test_invalid_username(client):
    resp = client.get("/api/v1/profiles/not a user")
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_username"


def test_api_key_required_when_configured(app_factory, ig):
    client = app_factory(api_keys=["secret"])
    mock_profile(ig)
    mock_clips(ig)
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/profiles/natgeo").status_code == 401
    ok = client.get("/api/v1/profiles/natgeo", headers={"X-API-Key": "secret"})
    assert ok.status_code == 200


def test_batch_job(client, ig):
    mock_profile(ig)
    mock_clips(ig)
    mock_profile(ig, "ghost", payload={"data": {"user": None}})

    created = client.post("/api/v1/jobs", json={"usernames": ["natgeo", "@NatGeo", "ghost"]})
    assert created.status_code == 202
    job = created.json()
    assert job["total"] == 2  # deduplicated

    final = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert final["status"] == "partial"
    by_user = {r["username"]: r for r in final["results"]}
    assert by_user["natgeo"]["status"] == "ok"
    assert by_user["ghost"]["status"] == "error"
    assert client.get("/api/v1/jobs/nope").status_code == 404


def test_list_and_delete(client, ig):
    mock_profile(ig)
    mock_clips(ig)
    client.get("/api/v1/profiles/natgeo")
    listing = client.get("/api/v1/profiles").json()
    assert listing["total"] == 1 and listing["items"][0]["username"] == "natgeo"

    assert client.delete("/api/v1/profiles/natgeo").status_code == 204
    assert client.get("/api/v1/profiles").json()["total"] == 0
    assert client.get("/api/v1/profiles/natgeo/history").status_code == 404


def test_media_download_and_serving(app_factory, ig, tmp_path: Path):
    client = app_factory(download_media=True)
    payload = copy.deepcopy(load("web_profile_info.json"))
    mock_profile(ig, payload=payload)
    mock_clips(ig)
    ig.get(url__startswith="https://cdn.example.com/").respond(200, content=b"\xff\xd8jpeg-bytes")

    body = client.get("/api/v1/profiles/natgeo").json()
    assert body["profile"]["profile_pic_local"] == "/media/natgeo/profile.jpg"
    assert (tmp_path / "storage/media/natgeo/profile.jpg").read_bytes() == b"\xff\xd8jpeg-bytes"
    assert body["posts"][0]["thumbnail_local"] == "/media/natgeo/AAA111.jpg"
    assert client.get("/media/natgeo/profile.jpg").content == b"\xff\xd8jpeg-bytes"

    cached = client.get("/api/v1/profiles/natgeo").json()
    assert cached["source"] == "cache"
    assert cached["profile"]["profile_pic_local"] == "/media/natgeo/profile.jpg"
