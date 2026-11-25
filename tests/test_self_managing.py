"""Self-management: account pool, auto-login, cooldowns, proxies, alerts, secrets."""

import base64
import json
import os
import sqlite3

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.public import PrivateKey, SealedBox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.alerts import Alerter
from app.core.crypto import SecretBox
from app.db.models import AccountRow
from app.db.session import create_engine, init_db
from app.scraper import endpoints as ep
from app.scraper.accounts import AccountPool
from app.scraper.login import encrypt_password
from app.scraper.proxies import ProxyRouter
from tests.conftest import load, make_settings, mock_clips, mock_profile

IG_KEY = PrivateKey.generate()  # stands in for Instagram's password-encryption key
HOOK = "https://hooks.example.com/alert"


def mock_login(ig, *responses):
    ig.get(ep.SHARED_DATA).respond(
        200,
        json={
            "config": {"csrf_token": "csrf123"},
            "encryption": {
                "key_id": "41",
                "public_key": IG_KEY.public_key.encode().hex(),
                "version": "10",
            },
        },
    )
    return ig.post(ep.LOGIN_AJAX).mock(side_effect=list(responses))


def login_ok(session: str = "555%3Asess1") -> httpx.Response:
    return httpx.Response(
        200,
        json={"authenticated": True, "user": True, "userId": "555", "status": "ok"},
        headers={"set-cookie": f"sessionid={session}; Domain=.instagram.com; Path=/; HttpOnly"},
    )


def login_bad_password() -> httpx.Response:
    return httpx.Response(200, json={"authenticated": False, "user": True, "status": "ok"})


def login_checkpoint() -> httpx.Response:
    return httpx.Response(
        400,
        json={
            "message": "checkpoint_required",
            "checkpoint_url": "/challenge/x/",
            "status": "fail",
        },
    )


def mock_feed(ig, *responses):
    route = ig.get(ep.USER_FEED.format(user_id="787132"))
    if responses:
        return route.mock(side_effect=list(responses))
    return route.respond(200, json=load("feed_user.json"))


# --------------------------------------------------------------- crypto units
def test_encrypt_password_roundtrip():
    enc = encrypt_password(
        "s3cret!",
        key_id=41,
        public_key_hex=IG_KEY.public_key.encode().hex(),
        version="10",
        timestamp=1700000000,
    )
    prefix, version, ts, b64 = enc.split(":", 3)
    assert (prefix, version, ts) == ("#PWD_INSTAGRAM_BROWSER", "10", "1700000000")
    blob = base64.b64decode(b64)
    assert blob[0] == 1 and blob[1] == 41
    size = int.from_bytes(blob[2:4], "little")
    sealed, tag, ciphertext = blob[4 : 4 + size], blob[4 + size : 20 + size], blob[20 + size :]
    aes_key = SealedBox(IG_KEY).decrypt(sealed)
    assert AESGCM(aes_key).decrypt(bytes(12), ciphertext + tag, ts.encode()) == b"s3cret!"


def test_secret_box_autogenerates_and_reuses_key(tmp_path):
    box = SecretBox.from_settings(None, tmp_path)
    key_file = tmp_path / ".secret_key"
    assert key_file.exists() and oct(key_file.stat().st_mode)[-3:] == "600"
    token = box.encrypt("sessionid-value")
    assert "sessionid" not in token
    again = SecretBox.from_settings(None, tmp_path)
    assert again.decrypt(token) == "sessionid-value"
    assert again.fingerprint("pw") == box.fingerprint("pw") != box.fingerprint("pw2")
    assert SecretBox.from_settings("different", tmp_path).decrypt(token) is None


# -------------------------------------------------------------- proxy units
def test_proxy_router_gateway_and_sticky(tmp_path):
    router = ProxyRouter(
        make_settings(
            tmp_path,
            proxy_url="http://u:p@gw:7000",
            proxy_sticky_url="http://u-session-{session}:p@gw:7000",
        )
    )
    assert router.mode == "gateway"
    assert router.anonymous() == "http://u:p@gw:7000" and router.rotates(router.anonymous())
    alpha = router.for_account("alpha")
    assert alpha == router.for_account("alpha") != router.for_account("beta")
    assert "{session}" not in alpha and not router.rotates(alpha)


def test_proxy_provider_formats_are_normalized(tmp_path):
    settings = make_settings(tmp_path, proxies="1.2.3.4:8000:user:pass,5.6.7.8:9000,http://u:p@h:1")
    assert settings.proxies == [
        "http://user:pass@1.2.3.4:8000",
        "http://5.6.7.8:9000",
        "http://u:p@h:1",
    ]


def test_proxy_list_rests_failing_proxy(tmp_path):
    router = ProxyRouter(make_settings(tmp_path, proxies=["http://p1", "http://p2"]))
    assert {router.anonymous(), router.anonymous()} == {"http://p1", "http://p2"}
    for _ in range(3):
        router.report("http://p1", ok=False)
    assert all(router.anonymous() == "http://p2" for _ in range(4))
    assert router.for_account("anyone") == "http://p2"
    assert router.summary()["list_resting"] == 1


async def test_alerter_dedupes_and_posts_webhook(tmp_path, ig):
    hook = ig.post(HOOK).respond(200)
    alerts = Alerter(make_settings(tmp_path, alert_webhook_url=HOOK))
    assert await alerts.send("k", "hello")
    assert not await alerts.send("k", "hello again")  # same key within the repeat window
    assert await alerts.send("other", "x")
    assert hook.call_count == 2
    assert json.loads(hook.calls[0].request.content)["text"].endswith("hello")


# ------------------------------------------------------- pool via the API
def test_auto_login_unlocks_pagination_and_encrypts_session(app_factory, ig, tmp_path):
    client = app_factory(ig_accounts=["scraper1:pw-1"])
    login = mock_login(ig, login_ok())
    mock_profile(ig)
    mock_clips(ig)
    feed = mock_feed(ig)

    body = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 50}).json()
    assert len(body["posts"]) == 4
    assert login.call_count == 1
    form = login.calls.last.request.content.decode()
    assert "username=scraper1" in form and "PWD_INSTAGRAM_BROWSER%3A10%3A" in form
    assert "pw-1" not in form  # password is sent encrypted
    assert login.calls.last.request.headers["x-csrftoken"] == "csrf123"
    assert "sessionid=555%3Asess1" in feed.calls.last.request.headers["cookie"]

    ready = client.get("/health/ready").json()
    assert ready["status"] == "ready"
    assert ready["accounts"]["by_status"] == {"active": 1}
    assert ready["accounts"]["accounts"][0]["requests_last_hour"] == 1

    stored = sqlite3.connect(tmp_path / "test.db").execute("select session_enc from ig_accounts")
    session_enc = stored.fetchone()[0]
    assert session_enc and "sess1" not in session_enc


def test_checkpoint_is_flagged_alerted_and_scrape_still_returns(app_factory, ig):
    client = app_factory(ig_accounts=["scraper1:pw"], alert_webhook_url=HOOK)
    hook = ig.post(HOOK).respond(200)
    mock_login(ig, login_checkpoint())
    mock_profile(ig)
    mock_clips(ig)

    body = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 50}).json()
    assert len(body["posts"]) == 3  # logged-out data still served
    assert any("No Instagram account is available" in w for w in body["warnings"])
    assert hook.call_count == 1 and "checkpoint" in hook.calls.last.request.content.decode()

    ready = client.get("/health/ready").json()
    assert ready["status"] == "degraded"
    assert ready["accounts"]["by_status"] == {"checkpoint": 1}
    assert "checkpoint" in ready["recent_alerts"][0]["key"]


def test_bad_password_stops_trying(app_factory, ig):
    client = app_factory(ig_accounts=["scraper1:wrong"])
    login = mock_login(ig, login_bad_password())
    mock_profile(ig)
    mock_clips(ig)

    client.get("/api/v1/profiles/natgeo", params={"posts_limit": 50})
    again = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 50, "refresh": True})
    assert login.call_count == 1  # no hammering with a wrong password
    assert any("Add Instagram accounts" in w for w in again.json()["warnings"])
    by_status = client.get("/health/ready").json()["accounts"]["by_status"]
    assert by_status == {"bad_credentials": 1}


def test_rate_limited_account_rests_and_next_account_is_used(app_factory, ig):
    client = app_factory(ig_session_ids=["111%3Aaaa", "222%3Abbb"])
    mock_profile(ig)
    mock_clips(ig)
    feed = mock_feed(
        ig,
        httpx.Response(429, json={"message": "", "status": "fail"}),
        httpx.Response(200, json=load("feed_user.json")),
    )

    body = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 50}).json()
    assert len(body["posts"]) == 4
    cookies = [call.request.headers["cookie"] for call in feed.calls]
    assert "111%3Aaaa" in cookies[0] and "222%3Abbb" in cookies[1]
    by_status = client.get("/health/ready").json()["accounts"]["by_status"]
    assert by_status == {"active": 1, "cooling": 1}


def test_expired_session_logs_in_again(app_factory, ig):
    client = app_factory(ig_accounts=["scraper1:pw"])
    login = mock_login(ig, login_ok("555%3Afirst"), login_ok("555%3Asecond"))
    mock_profile(ig)
    mock_clips(ig)
    feed = mock_feed(
        ig,
        httpx.Response(401, json={"message": "login_required", "status": "fail"}),
        httpx.Response(200, json=load("feed_user.json")),
    )

    body = client.get("/api/v1/profiles/natgeo", params={"posts_limit": 50}).json()
    assert len(body["posts"]) == 4
    assert login.call_count == 2
    assert "555%3Asecond" in feed.calls[1].request.headers["cookie"]


def test_ready_hides_account_details_without_api_key(app_factory):
    client = app_factory(api_keys=["k"], ig_session_ids=["111%3Aaaa"])
    public = client.get("/health/ready").json()
    assert "accounts" not in public["accounts"] and "recent_alerts" not in public
    private = client.get("/health/ready", headers={"X-API-Key": "k"}).json()
    assert private["accounts"]["accounts"][0]["username"] == "session_111"


# ------------------------------------------------- pool maintenance (direct)
async def test_accounts_file_hot_reload_password_fix_and_removal(tmp_path, ig):
    accounts_file = tmp_path / "accounts.txt"
    accounts_file.write_text("# scraping accounts\nalpha:pw1\n")
    settings = make_settings(tmp_path, ig_accounts_file=accounts_file, alert_webhook_url=HOOK)
    engine = create_engine(settings.database_url)
    await init_db(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    pool = AccountPool(
        settings,
        sessions,
        SecretBox.from_settings("k", tmp_path),
        ProxyRouter(settings),
        Alerter(settings),
    )
    hook = ig.post(HOOK).respond(200)
    mock_login(ig, login_bad_password(), login_ok(), login_ok())

    def rewrite(text: str, stamp: int) -> None:
        accounts_file.write_text(text)
        os.utime(accounts_file, (stamp, stamp))

    try:
        await pool.sync()
        await pool.maintain()
        assert pool.counts() == {"bad_credentials": 1}
        alerts = [call.request.content.decode() for call in hook.calls]
        assert len(alerts) == 2  # wrong password + pool low
        assert any("Login failed" in a for a in alerts) and any("usable" in a for a in alerts)

        rewrite("alpha:pw2\nbeta:pw3\n", 2_000_000_000)  # fixed password + new account
        await pool.maintain()
        assert pool.counts() == {"active": 2}

        rewrite("beta:pw3\n", 2_000_000_100)  # alpha removed
        await pool.maintain()
        assert pool.counts() == {"active": 1}
        async with sessions() as session:
            alpha = await session.scalar(select(AccountRow).where(AccountRow.username == "alpha"))
            assert alpha is not None and alpha.status == "disabled"
    finally:
        await engine.dispose()
