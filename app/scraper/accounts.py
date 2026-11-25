"""Self-managing pool of Instagram accounts.

Account lifecycle (all automatic):

    needs_login --login ok--> active --rate limited--> cooling --(rest ends)--> active
         ^                      |
         +--session expired-----+
    login: checkpoint/2FA --> checkpoint  (a human approves in the app; retried every few hours)
    login: wrong password --> bad_credentials (retried once the password in config changes)
    removed from config   --> disabled

Passwords live only in config (env / accounts file). Sessions are stored encrypted.
"""

import asyncio
import hashlib
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import unquote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.alerts import Alerter
from app.core.config import Settings
from app.core.crypto import SecretBox
from app.core.errors import (
    Blocked,
    LoginRequired,
    NoAccountAvailable,
    ProfileNotFound,
    RateLimited,
    ScraperError,
)
from app.core.utils import as_utc, utcnow
from app.db.models import AccountRow
from app.scraper.login import LoginStatus, web_login
from app.scraper.proxies import ProxyRouter

log = logging.getLogger(__name__)

_EPOCH = datetime.min.replace(tzinfo=UTC)


class AccountStatus(StrEnum):
    ACTIVE = "active"
    NEEDS_LOGIN = "needs_login"
    COOLING = "cooling"
    CHECKPOINT = "checkpoint"
    BAD_CREDENTIALS = "bad_credentials"
    DISABLED = "disabled"


def session_user_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    uid = unquote(session_id).split(":", 1)[0]
    return uid if uid.isdigit() else None


@dataclass(eq=False)
class Account:
    username: str
    password: str | None = field(default=None, repr=False)
    status: AccountStatus = AccountStatus.NEEDS_LOGIN
    session_id: str | None = field(default=None, repr=False)
    user_id: str | None = None
    password_fp: str | None = None
    cooldown_until: datetime | None = None
    next_login_at: datetime | None = None
    strikes: int = 0  # consecutive rate limits (drives cooldown length)
    failures: int = 0  # consecutive other failures
    total_requests: int = 0
    total_failures: int = 0
    last_error: str | None = None
    last_used_at: datetime | None = None
    last_login_at: datetime | None = None
    in_use: bool = False
    recent: deque[float] = field(default_factory=deque)  # request timestamps (last hour)

    @property
    def can_login(self) -> bool:
        return self.password is not None

    def requests_last_hour(self) -> int:
        cutoff = time.time() - 3600
        while self.recent and self.recent[0] < cutoff:
            self.recent.popleft()
        return len(self.recent)


@dataclass
class Lease:
    account: Account
    proxy: str | None


def configured_accounts(settings: Settings) -> list[tuple[str, str | None, str | None]]:
    """(username, password, seed_session) from IG_ACCOUNTS, IG_ACCOUNTS_FILE, IG_SESSION_IDS."""
    entries = list(settings.ig_accounts)
    path = settings.ig_accounts_file
    if path is not None:
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    entries.append(line)
        else:
            log.warning("IG_ACCOUNTS_FILE %s does not exist", path)

    accounts: dict[str, tuple[str, str | None, str | None]] = {}
    for entry in entries:
        username, sep, password = entry.partition(":")
        username = username.strip().lstrip("@").lower()
        if not username or not sep or not password:
            log.warning("Ignoring malformed account entry (expected username:password)")
            continue
        accounts[username] = (username, password, None)
    for session_id in settings.ig_session_ids:
        uid = session_user_id(session_id) or hashlib.sha1(session_id.encode()).hexdigest()[:10]
        name = f"session_{uid}"
        accounts[name] = (name, None, session_id)
    return list(accounts.values())


def _opt_utc(value: datetime | None) -> datetime | None:
    return as_utc(value) if value is not None else None


class AccountPool:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        box: SecretBox,
        proxies: ProxyRouter,
        alerts: Alerter,
    ) -> None:
        self._s = settings
        self._sessions = sessions
        self._box = box
        self._proxies = proxies
        self._alerts = alerts
        self._accounts: dict[str, Account] = {}
        self._cond = asyncio.Condition()
        self._login_lock = asyncio.Lock()
        self._file_mtime: float | None = None

    # ------------------------------------------------------------ inspection
    @property
    def can_authenticate(self) -> bool:
        """Is there any account that is usable now or can become usable by itself?"""
        return any(
            a.status in (AccountStatus.ACTIVE, AccountStatus.COOLING)
            or (a.can_login and a.status in (AccountStatus.NEEDS_LOGIN, AccountStatus.CHECKPOINT))
            for a in self._accounts.values()
        )

    def counts(self) -> dict[str, int]:
        return dict(Counter(a.status.value for a in self._accounts.values()))

    def usable_now(self) -> int:
        return sum(
            1 for a in self._accounts.values() if a.status == AccountStatus.ACTIVE and a.session_id
        )

    def summary(self, *, details: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            "configured": len(self._accounts),
            "usable_now": self.usable_now(),
            "by_status": self.counts(),
        }
        if details:
            out["accounts"] = [
                {
                    "username": a.username,
                    "status": a.status.value,
                    "requests_last_hour": a.requests_last_hour(),
                    "total_requests": a.total_requests,
                    "total_failures": a.total_failures,
                    "cooldown_until": a.cooldown_until,
                    "next_login_at": a.next_login_at,
                    "last_used_at": a.last_used_at,
                    "last_login_at": a.last_login_at,
                    "last_error": a.last_error,
                    "can_relogin": a.can_login,
                }
                for a in sorted(self._accounts.values(), key=lambda a: a.username)
            ]
        return out

    def _describe(self) -> str:
        counts = self.counts()
        if not counts:
            return "no accounts configured; add IG_ACCOUNTS"
        text = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
        cooling = [a.cooldown_until for a in self._accounts.values() if a.cooldown_until]
        if cooling and counts.get(AccountStatus.COOLING.value):
            wait = max(0, int((min(cooling) - utcnow()).total_seconds() // 60))
            text += f"; next account back in ~{wait} min"
        return text

    # ---------------------------------------------------------- config sync
    async def sync(self) -> None:
        """Load accounts from config + DB. Safe to call again (accounts file hot-reload)."""
        configured = configured_accounts(self._s)
        names = {c[0] for c in configured}
        path = self._s.ig_accounts_file
        self._file_mtime = path.stat().st_mtime if path and path.exists() else None

        async with self._sessions() as session, session.begin():
            rows = {r.username: r for r in (await session.scalars(select(AccountRow))).all()}
            async with self._cond:
                for username, password, seed_session in configured:
                    acc = self._accounts.get(username)
                    if acc is None:
                        row = rows.get(username)
                        acc = self._from_row(row) if row else Account(username=username)
                        if acc.status in (AccountStatus.NEEDS_LOGIN, AccountStatus.CHECKPOINT):
                            acc.next_login_at = None  # restart = "I fixed it": try logging in now
                        self._accounts[username] = acc
                    self._reconcile(acc, password, seed_session)
                    self._write(session, acc, rows.get(username))
                for username in list(self._accounts):
                    if username not in names:
                        acc = self._accounts.pop(username)
                        acc.status = AccountStatus.DISABLED
                        self._write(session, acc, rows.get(username))
                for username, row in rows.items():
                    if username not in names and row.status != AccountStatus.DISABLED:
                        row.status = AccountStatus.DISABLED.value
                        row.updated_at = utcnow()
                self._cond.notify_all()
        log.info("Account pool synced: %s", self._describe())

    def _reconcile(self, acc: Account, password: str | None, seed_session: str | None) -> None:
        acc.password = password
        fingerprint = self._box.fingerprint(password) if password else None
        if acc.status == AccountStatus.DISABLED:
            acc.status = AccountStatus.NEEDS_LOGIN
        if fingerprint != acc.password_fp and acc.status in (
            AccountStatus.BAD_CREDENTIALS,
            AccountStatus.CHECKPOINT,
        ):
            acc.status, acc.next_login_at = AccountStatus.NEEDS_LOGIN, None  # new password: retry
        acc.password_fp = fingerprint
        if seed_session and seed_session != acc.session_id:
            acc.session_id = seed_session
            acc.user_id = session_user_id(seed_session)
            acc.status = AccountStatus.ACTIVE
        if acc.status in (AccountStatus.ACTIVE, AccountStatus.COOLING) and not acc.session_id:
            acc.status = AccountStatus.NEEDS_LOGIN

    def _file_changed(self) -> bool:
        path = self._s.ig_accounts_file
        if path is None:
            return False
        mtime = path.stat().st_mtime if path.exists() else None
        return mtime != self._file_mtime

    # ----------------------------------------------------------- persistence
    def _from_row(self, row: AccountRow) -> Account:
        return Account(
            username=row.username,
            status=AccountStatus(row.status),
            session_id=self._box.decrypt(row.session_enc) if row.session_enc else None,
            user_id=row.user_id,
            password_fp=row.password_fp,
            cooldown_until=_opt_utc(row.cooldown_until),
            next_login_at=_opt_utc(row.next_login_at),
            strikes=row.strikes or 0,
            failures=row.failures or 0,
            total_requests=row.total_requests or 0,
            total_failures=row.total_failures or 0,
            last_error=row.last_error,
            last_used_at=_opt_utc(row.last_used_at),
            last_login_at=_opt_utc(row.last_login_at),
        )

    def _write(self, session: AsyncSession, acc: Account, row: AccountRow | None) -> None:
        if row is None:
            row = AccountRow(username=acc.username, created_at=utcnow())
            session.add(row)
        row.status = acc.status.value
        row.session_enc = self._box.encrypt(acc.session_id) if acc.session_id else None
        row.user_id = acc.user_id
        row.password_fp = acc.password_fp
        row.strikes = acc.strikes
        row.failures = acc.failures
        row.total_requests = acc.total_requests
        row.total_failures = acc.total_failures
        row.last_error = acc.last_error
        row.cooldown_until = acc.cooldown_until
        row.next_login_at = acc.next_login_at
        row.last_used_at = acc.last_used_at
        row.last_login_at = acc.last_login_at
        row.updated_at = utcnow()

    async def _persist(self, acc: Account) -> None:
        async with self._sessions() as session, session.begin():
            self._write(session, acc, await session.get(AccountRow, acc.username))

    async def flush(self) -> None:
        if not self._accounts:
            return
        async with self._sessions() as session, session.begin():
            query = select(AccountRow).where(AccountRow.username.in_(list(self._accounts)))
            rows = {r.username: r for r in (await session.scalars(query)).all()}
            for acc in list(self._accounts.values()):
                self._write(session, acc, rows.get(acc.username))

    # -------------------------------------------------------- acquire/release
    def _lift_cooldowns(self) -> None:
        now = utcnow()
        for acc in self._accounts.values():
            if acc.status == AccountStatus.COOLING and (acc.cooldown_until or _EPOCH) <= now:
                acc.status, acc.cooldown_until = AccountStatus.ACTIVE, None
                log.info("Account %s is back from cooldown", acc.username)

    def _pick(self) -> Account | None:
        self._lift_cooldowns()
        ready = [
            a
            for a in self._accounts.values()
            if a.status == AccountStatus.ACTIVE
            and a.session_id
            and not a.in_use
            and a.requests_last_hour() < self._s.account_max_requests_per_hour
        ]
        return min(ready, key=lambda a: a.last_used_at or _EPOCH) if ready else None

    def _due_for_login(self) -> list[Account]:
        now = utcnow()
        return [
            a
            for a in self._accounts.values()
            if a.can_login
            and not a.in_use
            and a.status in (AccountStatus.NEEDS_LOGIN, AccountStatus.CHECKPOINT)
            and (a.next_login_at is None or a.next_login_at <= now)
        ]

    async def acquire(self) -> Lease:
        """Reserve the least-recently-used healthy account (logging one in if needed)."""
        async with self._cond:
            ready = self._pick() is not None
        if not ready and self._due_for_login():
            await self.login_due(max_logins=1)

        deadline = time.monotonic() + self._s.account_wait_seconds
        async with self._cond:
            while True:
                acc = self._pick()
                if acc is not None:
                    acc.in_use = True
                    acc.recent.append(time.time())
                    acc.last_used_at = utcnow()
                    acc.total_requests += 1
                    return Lease(acc, self._proxies.for_account(acc.username))
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not any(a.in_use for a in self._accounts.values()):
                    break
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                except TimeoutError:
                    break
        raise NoAccountAvailable(
            f"No Instagram account is available right now ({self._describe()})."
        )

    async def release(self, lease: Lease, error: ScraperError | None) -> None:
        acc = lease.account
        async with self._cond:
            acc.in_use = False
            before = acc.status
            alert = self._apply_outcome(acc, error)
            self._cond.notify_all()
        if acc.status != before:
            await self._persist(acc)
        if alert:
            await self._alerts.send(*alert)

    def _apply_outcome(self, acc: Account, error: ScraperError | None) -> tuple[str, str] | None:
        if error is None or isinstance(error, ProfileNotFound):
            acc.strikes = acc.failures = 0
            return None

        now = utcnow()
        acc.total_failures += 1
        acc.last_error = error.message[:500]
        if isinstance(error, RateLimited):
            acc.strikes += 1
            rest = min(
                self._s.account_cooldown_seconds * 2 ** (acc.strikes - 1),
                self._s.account_cooldown_max_seconds,
            )
            acc.status, acc.cooldown_until = AccountStatus.COOLING, now + timedelta(seconds=rest)
            log.warning("Account %s rate limited; resting %ss", acc.username, rest)
            return None
        if isinstance(error, LoginRequired):
            acc.session_id = None
            acc.status, acc.next_login_at = AccountStatus.NEEDS_LOGIN, now
            log.info("Session of %s expired; will log in again", acc.username)
            return None
        if isinstance(error, Blocked):
            acc.session_id = None
            acc.status = AccountStatus.CHECKPOINT
            acc.next_login_at = now + timedelta(seconds=self._s.account_checkpoint_retry_seconds)
            return (f"checkpoint:{acc.username}", self._checkpoint_message(acc))
        acc.failures += 1
        return None

    def _checkpoint_message(self, acc: Account) -> str:
        hours = self._s.account_checkpoint_retry_seconds / 3600
        if not acc.can_login:
            return (
                f"Instagram account {acc.username} hit a security checkpoint. It has no password "
                "configured, so add it to IG_ACCOUNTS or provide a fresh sessionid."
            )
        return (
            f"Instagram account {acc.username} hit a security checkpoint or 2FA. Open Instagram "
            f"with this account and approve the login ('This was me'), and turn off 2FA for "
            f"scraping accounts. It will be retried automatically every {hours:g}h."
        )

    # ----------------------------------------------------------------- login
    async def login_due(self, max_logins: int | None = None) -> int:
        due = self._due_for_login()
        if max_logins is not None:
            due = due[:max_logins]
        logged_in = 0
        for acc in due:
            logged_in += await self._login(acc)
        return logged_in

    async def _login(self, acc: Account) -> bool:
        alert: tuple[str, str] | None = None
        async with self._login_lock:
            if acc.status == AccountStatus.ACTIVE and acc.session_id:
                return True  # someone else logged it in while we waited
            if acc.password is None:
                return False
            proxy = self._proxies.for_account(acc.username)
            async with httpx.AsyncClient(
                http2=True, proxy=proxy, timeout=self._s.request_timeout, follow_redirects=False
            ) as http:
                result = await web_login(http, acc.username, acc.password, self._s)

            now = utcnow()
            acc.last_login_at = now
            if result.status == LoginStatus.OK:
                acc.session_id = result.session_id
                acc.user_id = result.user_id or session_user_id(result.session_id)
                acc.status = AccountStatus.ACTIVE
                acc.strikes = acc.failures = 0
                acc.last_error = acc.next_login_at = acc.cooldown_until = None
                log.info("Logged in Instagram account %s", acc.username)
            elif result.status == LoginStatus.BAD_CREDENTIALS:
                acc.status, acc.next_login_at = AccountStatus.BAD_CREDENTIALS, None
                acc.last_error = result.message
                alert = (
                    f"bad_credentials:{acc.username}",
                    f"Login failed for Instagram account {acc.username} ({result.message}). "
                    "Fix the password in the accounts config; it is retried automatically "
                    "once the password changes.",
                )
            elif result.status in (LoginStatus.CHECKPOINT, LoginStatus.TWO_FACTOR):
                acc.status = AccountStatus.CHECKPOINT
                acc.next_login_at = now + timedelta(
                    seconds=self._s.account_checkpoint_retry_seconds
                )
                acc.last_error = result.message
                alert = (f"checkpoint:{acc.username}", self._checkpoint_message(acc))
            else:
                acc.status = AccountStatus.NEEDS_LOGIN
                acc.next_login_at = now + timedelta(seconds=self._s.account_login_retry_seconds)
                acc.last_error = f"login {result.status.value}: {result.message}"
                log.warning("Login for %s failed: %s", acc.username, acc.last_error)
            await self._persist(acc)

        async with self._cond:
            self._cond.notify_all()
        if alert:
            await self._alerts.send(*alert, level="error")
        return result.status == LoginStatus.OK

    # ----------------------------------------------------------- maintenance
    async def maintain(self) -> None:
        """One self-management pass (run periodically by the Supervisor)."""
        if self._file_changed():
            log.info("Accounts file changed; re-syncing pool")
            await self.sync()
        async with self._cond:
            self._lift_cooldowns()
            self._cond.notify_all()
        await self.login_due()
        for acc in list(self._accounts.values()):
            if acc.status == AccountStatus.NEEDS_LOGIN and not acc.can_login:
                await self._alerts.send(
                    f"session_expired:{acc.username}",
                    f"Session-only account {acc.username} expired. Add the account with its "
                    "password to IG_ACCOUNTS so it can re-login by itself.",
                )
        await self.flush()
        if self._accounts and self.usable_now() < self._s.alert_min_healthy_accounts:
            await self._alerts.send(
                "pool_low",
                f"Only {self.usable_now()} of {len(self._accounts)} Instagram accounts are "
                f"usable ({self._describe()}). Add accounts or fix the flagged ones.",
            )
