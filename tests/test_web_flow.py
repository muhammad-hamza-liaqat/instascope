"""The web-app (GraphQL) flow used when the old JSON API is not served."""

import itertools
import json
from urllib.parse import parse_qs

import httpx

from app.scraper import endpoints as ep
from app.scraper.web import jazoest, parse_tokens
from tests.conftest import FIXTURES, load

PAGE = "https://www.instagram.com/natgeo/"


def page_html() -> str:
    return (FIXTURES / "profile_page.html").read_text()


def gql_router(ig, *, reels_status: int = 200, posts=("gql_posts_p1.json", "gql_posts_p2.json")):
    """Answer /api/graphql by the query name in the form body; record what was sent."""
    sent: list[dict] = []
    pages = itertools.cycle(posts)

    def handler(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        sent.append(form)
        name = form["fb_api_req_friendly_name"]
        if name == "PolarisProfilePageContentQuery":
            return httpx.Response(200, json=load("gql_profile.json"))
        if name == "PolarisProfilePostsTabContentQuery_connection":
            return httpx.Response(200, json=load(next(pages)))
        if name == "PolarisProfileReelsTabContentQuery":
            if reels_status != 200:
                return httpx.Response(reels_status, html="<html>Page Not Found</html>")
            return httpx.Response(200, text=json.dumps(load("gql_reels.json")) + '\n{"x":1}')
        return httpx.Response(400, json={"errors": [{"message": "unknown query"}]})

    ig.post(ep.GRAPHQL).mock(side_effect=handler)
    return sent


def legacy_not_served(ig):
    return ig.get(ep.WEB_PROFILE_INFO).respond(404, html="<html>Page Not Found</html>")


def test_loads_first_strips_prefix_and_extra_documents():
    from app.scraper.web import loads_first

    assert loads_first('for (;;);{"data":{"a":1}}') == {"data": {"a": 1}}
    assert loads_first('{"data":1}\n{"label":"deferred"}') == {"data": 1}


def test_parse_tokens_and_jazoest():
    tokens = parse_tokens(page_html(), "natgeo")
    assert tokens.lsd == "LSDTOKEN123"
    assert tokens.fb_dtsg == "DTSG:TOKEN:456"
    assert tokens.viewer_id == "17841458875061498"
    assert tokens.profile_id == "787132"
    assert jazoest("ab") == "2" + str(ord("a") + ord("b"))


def test_web_flow_returns_profile_posts_reels(app_factory, ig):
    client = app_factory(ig_session_ids=["58811986502%3Asess"])
    legacy = legacy_not_served(ig)
    page = ig.get(PAGE).respond(200, html=page_html())
    sent = gql_router(ig)

    body = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 3}).json()
    assert body["profile"]["followers"] == 267_000_000
    assert body["profile"]["biography"] == "Step into wonder"
    assert body["profile"]["profile_pic_url_hd"] == "https://cdn.example.com/pic_hd.jpg"
    assert body["profile"]["posts_count"] == 32209
    assert [p["shortcode"] for p in body["posts"]] == ["P1", "R1", "P3"]
    assert [r["shortcode"] for r in body["reels"]] == ["R1", "R2"]
    assert body["reels"][0]["plays"] == 50000

    # It looked like the browser: page cookie + tokens, same account for every call.
    assert "sessionid=58811986502%3Asess" in page.calls.last.request.headers["cookie"]
    names = [f["fb_api_req_friendly_name"] for f in sent]
    assert names[0] == "PolarisProfilePageContentQuery"
    assert sent[0]["lsd"] == "LSDTOKEN123" and sent[0]["fb_dtsg"] == "DTSG:TOKEN:456"
    assert sent[0]["av"] == "17841458875061498"
    assert json.loads(sent[0]["variables"])["id"] == "787132"
    second_posts = [
        f for f in sent if f["fb_api_req_friendly_name"].startswith("PolarisProfilePosts")
    ][1]
    assert json.loads(second_posts["variables"])["after"] == "CURSOR2"

    # The old API is skipped for a while once it is known to be dead.
    client.get("/api/v1/profiles/natgeo", params={"refresh": True, "posts_limit": 2})
    assert legacy.call_count == 2  # first scrape only (logged-out + logged-in)


def test_web_flow_finds_id_from_posts_when_page_has_none(app_factory, ig):
    client = app_factory(ig_session_ids=["58811986502%3Asess"])
    legacy_not_served(ig)
    html = page_html().replace('"profile_id":"787132"', "").replace("profilePage_787132", "x")
    ig.get(PAGE).respond(200, html=html)
    sent = gql_router(ig)
    body = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 2}).json()
    assert body["profile"]["id"] == "787132"
    assert sent[0]["fb_api_req_friendly_name"].startswith("PolarisProfilePosts")


def test_web_flow_reels_fallback_to_posts(app_factory, ig):
    client = app_factory(ig_session_ids=["58811986502%3Asess"])
    legacy_not_served(ig)
    ig.get(PAGE).respond(200, html=page_html())
    gql_router(ig, reels_status=404)
    body = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 3}).json()
    assert [r["shortcode"] for r in body["reels"]] == ["R1"]
    assert any("Reels tab unavailable" in w for w in body["warnings"])


def test_web_flow_login_redirect_flags_account(app_factory, ig):
    client = app_factory(ig_session_ids=["58811986502%3Asess"])
    legacy_not_served(ig)
    ig.get(PAGE).respond(302, headers={"location": "https://www.instagram.com/accounts/login/"})
    resp = client.get("/api/v1/profiles/natgeo")
    assert resp.status_code == 503
    assert client.get("/health/ready").json()["accounts"]["by_status"] == {"needs_login": 1}


def test_web_flow_unknown_profile(app_factory, ig):
    client = app_factory(ig_session_ids=["58811986502%3Asess"])
    ig.get(ep.WEB_PROFILE_INFO).respond(404, html="<html>Page Not Found</html>")
    ig.get("https://www.instagram.com/ghost_user_x/").respond(404, html="<html>Not found</html>")
    resp = client.get("/api/v1/profiles/ghost_user_x")
    assert resp.status_code == 404
    assert client.get("/health/ready").json()["accounts"]["by_status"] == {"active": 1}


def test_wrong_id_on_page_is_corrected_from_posts_owner(app_factory, ig):
    client = app_factory(ig_session_ids=["58811986502%3Asess"])
    legacy_not_served(ig)
    ig.get(PAGE).respond(200, html=page_html().replace("787132", "58811986502"))
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        sent.append(form)
        name = form["fb_api_req_friendly_name"]
        if name == "PolarisProfilePageContentQuery":
            if json.loads(form["variables"])["id"] != "787132":
                return httpx.Response(200, json={"data": {"user": None}})
            return httpx.Response(200, json=load("gql_profile.json"))
        if name.startswith("PolarisProfilePosts"):
            return httpx.Response(200, json=load("gql_posts_p2.json"))
        return httpx.Response(200, json=load("gql_reels.json"))

    ig.post(ep.GRAPHQL).mock(side_effect=handler)
    body = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 1}).json()
    assert body["profile"]["username"] == "natgeo" and body["profile"]["id"] == "787132"


def test_empty_profile_reply_explains_why(app_factory, ig):
    client = app_factory(ig_session_ids=["58811986502%3Asess"])
    legacy_not_served(ig)
    ig.get(PAGE).respond(200, html=page_html())

    def handler(request: httpx.Request) -> httpx.Response:
        name = parse_qs(request.content.decode())["fb_api_req_friendly_name"][0]
        if name == "PolarisProfilePageContentQuery":
            return httpx.Response(
                200, json={"data": {"user": None}, "errors": [{"message": "missing variable"}]}
            )
        return httpx.Response(200, json=load("gql_posts_p2.json"))

    ig.post(ep.GRAPHQL).mock(side_effect=handler)
    detail = client.get("/api/v1/profiles/natgeo").json()["detail"]
    assert "id 787132" in detail and "missing variable" in detail


def test_facebook_style_error_is_reported(app_factory, ig):
    client = app_factory(ig_session_ids=["58811986502%3Asess"])
    legacy_not_served(ig)
    ig.get(PAGE).respond(200, html=page_html())
    ig.post(ep.GRAPHQL).respond(
        200,
        text='for (;;);{"__ar":1,"error":1357054,"errorSummary":"Sorry, something went wrong",'
        '"errorDescription":"Please try closing and re-opening your browser window.","payload":null}',
    )
    detail = client.get("/api/v1/profiles/natgeo").json()["detail"]
    assert "1357054" in detail and "Sorry, something went wrong" in detail


def test_page_params_and_real_csrf_are_sent(app_factory, ig):
    client = app_factory(ig_session_ids=["58811986502%3Asess"])
    legacy_not_served(ig)
    html = page_html().replace(
        "</body>",
        '<script>{"haste_session":"20782.HYP","client_revision":1056203930,'
        '"__spin_r":1056203930,"__spin_b":"trunk","__spin_t":1795609117,'
        '"X-IG-App-ID":"936619743392459"}</script></body>',
    )
    ig.get(PAGE).respond(
        200,
        html=html,
        headers={"set-cookie": "csrftoken=REALcsrf123; Domain=.instagram.com; Path=/"},
    )
    sent = gql_router(ig)
    graphql = ig.routes[-1]
    client.get("/api/v1/profiles/natgeo", params={"posts_limit": 1})
    form = sent[0]
    assert form["__hs"] == "20782.HYP" and form["__rev"] == "1056203930"
    assert form["__spin_b"] == "trunk"
    request = graphql.calls[0].request
    assert request.headers["x-csrftoken"] == "REALcsrf123"
    assert "csrftoken=REALcsrf123" in request.headers["cookie"]
