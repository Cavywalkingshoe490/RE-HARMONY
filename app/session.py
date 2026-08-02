"""Logitech (LIP) + Harmony login, with the password in the OS keychain.

The password never touches a file in this project: it goes to
`keyring.set_password` (Keychain on macOS, Credential Manager on Windows) and
nowhere else. The tokens go to `~/.harmony_token.json`, written atomically
and with mode 0600.

## THE CLIENT_ID IS NOT IN HERE, and that is the point

`accounts.logi.com` wants a `client_id` to sign in, and that identifier is
Logitech's, not this project's: it lives in `R.string.logitech_app_id` inside
their Android app. So it is not distributed. What IS distributed is the tool
that reads it out of YOUR OWN copy of the APK:

    python3 config_work/extract_client_id.py <your.apk> --save

Three places, highest priority first: `RE_HARMONY_CLIENT_ID` in the
environment, `account.json` at the root of the project (which is not
published), and if neither is there, every entry point stops with
`SessionError` and those instructions instead of sending an identifier that
cannot work -- without a valid one the account service answers 401 and never
gets as far as looking at the password. This is the same resolution
`config_work/myharmony.py` does: one rule, written twice, on purpose (this
module must not import a research script, and that script must not import
the app).

## Nothing private is imported here any more

This module used to import a separate package that was never part of this
repository, and it dragged `httpx` in with it. Both are gone. The three
calls it needs -- LIP sign-in, LIP refresh, Harmony bootstrap -- are JSON
over HTTPS and `urllib.request` from the standard library serves them. The
only third-party import left is `keyring`, which the app already declares as
a dependency.

The Catalog screen came from that same package and is here too now, in
`app/catalog_client.py`: it takes the `HarmonySession` this module hands
back and borrows `JsonClient` so its reads travel on the same cookie jar as
the bootstrap that opened the session.

## Renewal cascade

`harmony_signin` fails with `TokenRejectedError` -> `lip_refresh` with
whatever `refresh_token` is on hand -> if that also fails (missing or
expired) and there's a password saved in the keychain -> `lip_login` from
scratch. Only if none of the three works is a manual login requested
(`SessionError`, never with the password in the message).

## ONE type crosses the seam, and it is `HarmonySession`

This module used to hand out two different things that both meant "you are
logged in": `ensure_valid_tokens()` -> `LipTokens` and `ensure_session()` ->
`HarmonySession`. `catalog_client.py` was written against the first and
named after the second, so calling the obvious one and handing the result to
the catalog blew up three frames deep with

    AttributeError: 'HarmonySession' object has no attribute 'access_token'

Neither module was wrong on its own. They were wrong TOGETHER, and nothing
measured them together: each one's console check passed.

So there is one type now. `HarmonySession` CARRIES the `LipTokens` that
validated it (`.tokens`), and it is what `ensure_session()` returns and what
`catalog.py`/`catalog_client.py` accept. `LipTokens` did not disappear --
it is still what LIP hands back and what goes to disk -- but it is no longer
a second thing the rest of the app has to know how to tell apart. And there
is one ENTRY POINT: `ensure_valid_tokens()` is gone, not kept as an alias.
An alias would be the same coin flip with a nicer implementation; whoever
wants only the bearer writes `ensure_session(email).tokens` and can see
themselves doing it.

`app/catalog_client.py`'s console check walks that seam offline
(`ensure_session` -> `GlobalCatalogClient` -> `search`, with the HTTP layer
stubbed), and `check.py --imports` walks it across all three modules. A
check that only proves each module IMPORTS cannot see a seam.

This is a normal Logitech account login: the user does not have a Harmony
Hub and this module does not need one.

Console check (no network, no keychain, no account):

    python3 app/session.py
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

import keyring
import keyring.errors

# ---------------------------------------------------------------------------
# Public addresses of Logitech's service. These are endpoints, not secrets:
# they are the same three URLs any packet capture of the mobile app shows.
# ---------------------------------------------------------------------------
LIP_SIGNIN_URL = "https://accounts.logi.com/identity/signin"
LIP_REFRESH_URL = "https://accounts.logi.com/identity/refresh"
HARMONY_SIGNIN_URL = (
    "https://lb-svcs.myharmony.com/CompositeSecurityServices/Security.svc/json2/signin"
)

SERVICE_NAME = "re-harmony"  # keyring service; the account is the email
DEFAULT_TOKEN_FILE = Path.home() / ".harmony_token.json"

#: Where `extract_client_id.py --save` leaves the identifier. It sits at
#: the root of the project and it is never published.
ACCOUNT_FILE = Path(__file__).resolve().parent.parent / "account.json"

#: The `User-Agent` the mobile client sends is Logitech's string too, so it is
#: not written here either. The default is this project announcing itself,
#: which is the honest thing for a third-party client to do; whoever needs to
#: look exactly like the Android app can say so out loud with
#: `RE_HARMONY_USER_AGENT` or with `"user_agent"` in `account.json`.
DEFAULT_USER_AGENT = "RE-HARMONY/0.1"

TIMEOUT = 30.0


class SessionError(RuntimeError):
    """App session error. Never includes the password in the message."""


class AuthenticationError(SessionError):
    """The Logitech identity was not accepted."""


class TokenRejectedError(AuthenticationError):
    """The tokens expired or were rejected, and can be refreshed."""


class ProtocolError(SessionError):
    """The answer did not respect the contract observed in the client."""


MISSING_CLIENT_ID = (
    "Logitech's client_id is missing, and without it their account service "
    "answers 401.\n"
    "It does not ship with this project: it is Logitech's credential, not "
    "ours.\n"
    "Pull it out of your own copy of the Harmony APK:\n"
    "    python3 config_work/extract_client_id.py <your.apk> --save\n"
    "or pass it in the environment with RE_HARMONY_CLIENT_ID."
)


# ---------------------------------------------------------------------------
# the identifier, and the file it may come from
# ---------------------------------------------------------------------------
def _from_account_file(key: str) -> str | None:
    """`key` out of `account.json`, or `None`. Never raises: a broken or
    absent file is the same thing as not having the value."""
    try:
        decoded = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    value = decoded.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def client_id() -> str | None:
    """Logitech's `client_id`: environment, then `account.json`, then nothing.

    Read on every call and not frozen at import time on purpose -- the app is
    long-lived, and somebody who writes `account.json` while it is open should
    be able to log in without restarting it.
    """
    from_environment = os.environ.get("RE_HARMONY_CLIENT_ID", "").strip()
    if from_environment:
        return from_environment
    return _from_account_file("client_id")


def _need_client_id() -> str:
    value = client_id()
    if not value:
        raise SessionError(MISSING_CLIENT_ID)
    return value


def user_agent() -> str:
    from_environment = os.environ.get("RE_HARMONY_USER_AGENT", "").strip()
    if from_environment:
        return from_environment
    return _from_account_file("user_agent") or DEFAULT_USER_AGENT


# ---------------------------------------------------------------------------
# what travels between this module and the rest of the app
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LipTokens:
    """The three tokens LIP hands back.

    What goes to `~/.harmony_token.json` and what the bearer is read from.
    It does NOT travel to the rest of the app on its own: it travels inside
    the `HarmonySession` that was opened with it (see below), so there is
    only one thing to pass and only one thing to get right.
    """

    access_token: str
    id_token: str
    refresh_token: str | None = None

    @classmethod
    def from_mapping(cls, value: object) -> LipTokens:
        if not isinstance(value, dict):
            raise ProtocolError("the LIP answer is not a JSON object")
        access = value.get("access_token")
        identity = value.get("id_token")
        refresh = value.get("refresh_token")
        if not isinstance(access, str) or not isinstance(identity, str):
            raise ProtocolError("access_token or id_token is missing")
        return cls(
            access_token=access,
            id_token=identity,
            refresh_token=refresh if isinstance(refresh, str) else None,
        )

    def to_dict(self) -> dict[str, str]:
        result = {"access_token": self.access_token, "id_token": self.id_token}
        if self.refresh_token:
            result["refresh_token"] = self.refresh_token
        return result


@dataclass(frozen=True, slots=True)
class HarmonySession:
    """What the Harmony bootstrap hands back once the tokens are good.

    It carries `tokens` -- the very `LipTokens` that were accepted -- and
    that is the whole point: this is the ONE type that crosses the seam
    between this module and the catalog (see the module docstring). Whoever
    holds a session holds the bearer it was opened with, so nobody has to
    thread two values through, and nobody can thread the wrong one.
    """

    account_id: str
    auth_token: str
    email: str | None
    is_locked_out: bool
    cookies: dict[str, str]
    tokens: LipTokens

    def to_dict(self) -> dict[str, object]:
        """The summary that can be shown or logged.

        `tokens` is deliberately NOT in here: this is the dict that ends up
        in a status line or a bug report, and the bearer has no business
        travelling in one. Whoever needs the bearer asks for `.tokens` and
        can see themselves doing it.
        """
        return {
            "account_id": self.account_id,
            "auth_token": self.auth_token,
            "email": self.email,
            "is_locked_out": self.is_locked_out,
            "cookies": dict(sorted(self.cookies.items())),
        }


# ---------------------------------------------------------------------------
# the password: keychain and nothing else
# ---------------------------------------------------------------------------
def save_password(email: str, password: str) -> None:
    """Saves the password to the OS keychain. Never written to a project file."""
    keyring.set_password(SERVICE_NAME, email, password)


def load_password(email: str) -> str | None:
    return keyring.get_password(SERVICE_NAME, email)


def forget_password(email: str) -> None:
    try:
        keyring.delete_password(SERVICE_NAME, email)
    except keyring.errors.PasswordDeleteError:
        pass  # wasn't saved anymore; not an app error


# ---------------------------------------------------------------------------
# JSON over HTTPS, on the standard library
# ---------------------------------------------------------------------------
def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


def _decode(body: bytes) -> object:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return None


class JsonClient:
    """One exchange: a cookie jar plus JSON in and JSON out.

    The jar is what `httpx.Client` used to provide for free, and it matters
    twice over: the Harmony bootstrap answers with session cookies, which
    `ensure_session()` reports, and every catalog read afterwards has to
    travel on those same cookies -- which is why this class is public and
    `catalog_client.py` borrows it instead of opening a second socket with
    an empty jar.
    """

    def __init__(self, timeout: float = TIMEOUT) -> None:
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )
        self._timeout = timeout

    def __enter__(self) -> JsonClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cookies(self) -> dict[str, str]:
        return {cookie.name: cookie.value or "" for cookie in self._jar}

    def post(
        self,
        url: str,
        payload: object,
        *,
        token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, object]:
        """`(status, decoded body)`. Never lets an error's body go unread.

        The body of an `HTTPError` is the only thing that explains a
        rejection, and urllib throws it away if the exception is allowed to
        propagate -- which is how a 401 and a 403 become indistinguishable.
        Here the error is caught, its code kept, and the request rejected by
        the caller with the code in hand.

        `payload` is any JSON value, not just an object: Logitech's
        `GetGlobalDevices` takes a bare list of identifiers. `headers` adds
        to (and may override) the four sent by default -- the discovery call
        of the catalog needs `x-logitech-api-version`, and that is the whole
        reason this parameter exists.
        """
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST"
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", user_agent())
        if token:
            request.add_header("Authorization", "Bearer " + token)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.status, _decode(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _decode(error.read())
        except urllib.error.URLError as error:
            raise SessionError(
                "could not reach %s: %s" % (_host(url), error.reason)
            ) from None
        except OSError as error:  # timeouts and the rest of the socket layer
            raise SessionError("could not reach %s: %s" % (_host(url), error)) from None


def _rejected(what: str, status: int) -> str:
    """The message of a rejection. Deliberately does NOT echo the body: that
    body can carry back the email that was sent, and an error message is the
    one string that ends up pasted into a bug report."""
    hint = ""
    if status in (401, 403):
        hint = (
            " -- either the email/password is wrong, or the client_id is not "
            "the one Logitech's own client uses"
        )
    return "%s: HTTP %d%s" % (what, status, hint)


# ---------------------------------------------------------------------------
# the three calls
# ---------------------------------------------------------------------------
def lip_login(client: JsonClient, email: str, password: str) -> LipTokens:
    status, payload = client.post(
        LIP_SIGNIN_URL,
        {
            "email": email,
            "password": password,
            "client_id": _need_client_id(),
            "channel_id": str(uuid.uuid4()),
        },
    )
    if status != 200:
        raise AuthenticationError(_rejected("LIP rejected the login", status))
    return LipTokens.from_mapping(payload)


def lip_refresh(client: JsonClient, tokens: LipTokens) -> LipTokens:
    if not tokens.refresh_token:
        raise AuthenticationError("there is no refresh_token; a login is needed")
    status, payload = client.post(
        LIP_REFRESH_URL,
        {"refresh_token": tokens.refresh_token, "client_id": _need_client_id()},
    )
    if status != 200:
        raise AuthenticationError(_rejected("LIP rejected the renewal", status))
    return LipTokens.from_mapping(payload)


def harmony_signin(client: JsonClient, tokens: LipTokens) -> HarmonySession:
    status, payload = client.post(
        HARMONY_SIGNIN_URL,
        {"access_token": tokens.access_token, "id_token": tokens.id_token},
        token=tokens.access_token,
    )
    if status in (401, 403):
        raise TokenRejectedError("Harmony rejected the tokens: HTTP %d" % status)
    if status != 200:
        raise AuthenticationError("Harmony rejected the bootstrap: HTTP %d" % status)
    if not isinstance(payload, dict):
        raise ProtocolError("Harmony's signin did not return an object")
    account_id = payload.get("AccountId")
    auth_token = payload.get("AuthToken")
    email = payload.get("Email")
    if not isinstance(account_id, str) or not isinstance(auth_token, str):
        raise ProtocolError("Harmony's signin did not return AccountId/AuthToken")
    if payload.get("IsLockedOut") is True:
        raise AuthenticationError("the Harmony account is locked")
    return HarmonySession(
        account_id=account_id,
        auth_token=auth_token,
        email=email if isinstance(email, str) else None,
        is_locked_out=False,
        cookies=client.cookies(),
        tokens=tokens,  # the bearer this session was opened with, kept together
    )


# ---------------------------------------------------------------------------
# the token file
# ---------------------------------------------------------------------------
def load_lip_tokens(token_file: Path = DEFAULT_TOKEN_FILE) -> LipTokens:
    path = Path(token_file).expanduser()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SessionError(
            "there is no session file at %s: log in once with your Logitech "
            "email and password" % path
        ) from None
    except OSError as error:
        raise SessionError("could not read %s: %s" % (path, error)) from None
    try:
        decoded = json.loads(raw)
    except ValueError as error:
        raise ProtocolError("%s is not valid JSON: %s" % (path, error)) from None
    return LipTokens.from_mapping(decoded)


def save_lip_tokens(tokens: LipTokens, token_file: Path = DEFAULT_TOKEN_FILE) -> Path:
    """Writes the tokens 0600 and atomically: a temporary file in the same
    directory, created private from the first byte, and `os.replace` at the
    end. A half-written token file is never observable."""
    path = Path(token_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix="." + path.name + ".", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                tokens.to_dict(), stream, ensure_ascii=False, indent=2, sort_keys=True
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            path.chmod(0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


# ---------------------------------------------------------------------------
# what the app calls
# ---------------------------------------------------------------------------
def login(
    email: str,
    password: str,
    *,
    remember: bool = True,
    token_file: Path = DEFAULT_TOKEN_FILE,
) -> LipTokens:
    """Initial LIP login against accounts.logi.com. Persists the tokens (0600)
    and, if `remember`, the password in the keychain."""
    with JsonClient() as client:
        tokens = lip_login(client, email, password)
    save_lip_tokens(tokens, token_file)
    if remember:
        save_password(email, password)
    return tokens


def ensure_session(
    email: str,
    *,
    token_file: Path = DEFAULT_TOKEN_FILE,
) -> HarmonySession:
    """THE call the app makes to be logged in. One cascade, one return type.

    harmony_signin -> catch TokenRejectedError -> lip_refresh -> retry, and a
    third step on top: if the refresh also fails and there's a password saved
    in the keychain, it logs back in on its own. Persists any new token to
    `token_file`.

    Returns the `HarmonySession` the signin that VALIDATED the tokens
    produced -- not a second bootstrap on a fresh socket. That is not a
    micro-optimisation: this function used to call `harmony_signin` once to
    validate and then a second time to build the session, so a plain search
    opened three signins (two here, one more inside the catalog client) and
    the cookies of the last one landed on a jar nobody read.

    A missing `client_id` is NOT part of the cascade: it raises `SessionError`
    with instructions and stops there, because retrying with an identifier
    that does not exist just spends the account's attempts.
    """
    tokens = load_lip_tokens(token_file)
    with JsonClient() as client:
        try:
            return harmony_signin(client, tokens)
        except TokenRejectedError:
            pass

        try:
            tokens = lip_refresh(client, tokens)
        except AuthenticationError:
            password = load_password(email)
            if password is None:
                raise SessionError(
                    "the token expired, there is no valid refresh_token, and "
                    "there is no password saved in the keychain -- a manual "
                    "login is needed"
                ) from None
            tokens = lip_login(client, email, password)

        save_lip_tokens(tokens, token_file)
        return harmony_signin(client, tokens)  # validates the final result


# `ensure_valid_tokens()` USED TO BE HERE, and it is gone on purpose.
#
# It returned `LipTokens` where `ensure_session()` returns `HarmonySession`,
# for the same email and the same cascade. Two public functions that both
# mean "get me logged in" and hand back different types is not a
# convenience: it is a coin flip at every call site, and the app lost it
# (`AttributeError: 'HarmonySession' object has no attribute
# 'access_token'`, on the press of Search). Whoever wants only the bearer
# writes `ensure_session(email).tokens` -- and can see themselves doing it.


# ---------------------------------------------------------------------------
# console check -- no network, no keychain, no account
# ---------------------------------------------------------------------------
def _self_check() -> int:
    """What can be measured without an account: that the module says WHY it
    cannot log in, and that the token file it writes is private."""
    failures: list[str] = []

    identifier = client_id()
    if identifier:
        print(
            "client_id: present (%d chars, from the environment or account.json)"
            % len(identifier)
        )
    else:
        print("client_id: NOT present -- login stops with instructions")
        for line in MISSING_CLIENT_ID.splitlines():
            print("   |", line)

    # The message has to name the way out, or it is not a degradation, it is
    # a dead end.
    for expected in ("extract_client_id.py", "RE_HARMONY_CLIENT_ID"):
        if expected not in MISSING_CLIENT_ID:
            failures.append("the message does not name %s" % expected)

    # A missing session file must not surface as a bare FileNotFoundError.
    with tempfile.TemporaryDirectory() as scratch:
        absent = Path(scratch) / "nothing.json"
        try:
            load_lip_tokens(absent)
            failures.append("a missing token file did not raise")
        except SessionError as error:
            print("no token file -> %s" % type(error).__name__)
            if "log in" not in str(error):
                failures.append("the missing-file message does not say what to do")

        # And the file this writes is 0600, atomically.
        target = Path(scratch) / "token.json"
        written = save_lip_tokens(
            LipTokens(access_token="a", id_token="b", refresh_token="c"), target
        )
        mode = written.stat().st_mode & 0o777
        print("token file mode: %o" % mode)
        if os.name == "posix" and mode != 0o600:
            failures.append("the token file came out %o, not 600" % mode)
        again = load_lip_tokens(target)
        if again != LipTokens(access_token="a", id_token="b", refresh_token="c"):
            failures.append("the token file did not round-trip")

    # THE SEAM. What `ensure_session()` hands back has to be usable by the
    # catalog, and the only thing the catalog needs out of it is the bearer.
    # Measured here without an account: `harmony_signin` is driven with a
    # stand-in for the HTTP layer, and the session it builds is asked for
    # the tokens it was opened with. This is the check that was missing --
    # `to_dict()` is asked too, because the bearer must NOT be in it.
    class _StubClient:
        """Answers `post()` the way Harmony's signin does. Nothing else."""

        def post(self, *_args: object, **_kwargs: object) -> tuple[int, object]:
            return 200, {
                "AccountId": "acc-1",
                "AuthToken": "auth-1",
                "Email": "someone@example.com",
            }

        def cookies(self) -> dict[str, str]:
            return {".HarmonyAuth": "x"}

    seed = LipTokens(access_token="AAA", id_token="III", refresh_token="RRR")
    opened = harmony_signin(_StubClient(), seed)  # type: ignore[arg-type]
    print("session carries its tokens: %s" % (opened.tokens == seed))
    if opened.tokens != seed:
        failures.append("HarmonySession did not carry the tokens it was opened with")
    if opened.tokens.access_token != "AAA":
        failures.append("the bearer the catalog reads is not the one that was sent")
    if "AAA" in json.dumps(opened.to_dict(), sort_keys=True):
        failures.append("to_dict() carried the bearer into a loggable dict")

    print("user agent: %s" % user_agent())

    if failures:
        print("SESSION: FAILED")
        for failure in failures:
            print("  -", failure)
        return 1
    print("SESSION: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
