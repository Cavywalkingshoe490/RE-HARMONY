"""Logitech's public catalog, read only, on the standard library.

This is the half of `catalog.py` that talks to Logitech. It used to live
in a separate package that was never published, and that is the whole story
of the bug this file exists to close: `catalog.py` imported fine -- so
`api.FALTA` was empty and every gate said the app was complete -- and then
the Search button raised the moment somebody pressed it, because the import
that was missing sat INSIDE the function. A criterion that only measures
whether a module IMPORTS does not measure whether its buttons WORK.

## What did not travel, and what replaced it

Three things in that package could not be published, and none of them was
load-bearing:

* **Logitech's `client_id`.** It is not used here at all. Signing in is
  `app/session.py`'s job -- this module receives the `HarmonySession` that
  `ensure_session()` already opened and never sees an email or a password.
  The identifier is resolved in exactly one place in the app
  (`RE_HARMONY_CLIENT_ID`, then `account.json`), and that place is not this
  file.
* **The `User-Agent` of the Android app.** Every request here goes out with
  the `user_agent()` of `app/session.py` -- `re-harmony/0.1` unless the
  operator says otherwise. A third-party client announcing itself is the
  honest behaviour; impersonating the vendor's app is a decision the
  operator makes out loud with `RE_HARMONY_USER_AGENT`, not a default baked
  into the source.
* **`httpx`.** Four POSTs of JSON over HTTPS with a cookie jar. That is
  `JsonClient` in `app/session.py`, which is `urllib.request` from the
  standard library and already carried the Harmony bootstrap. Reusing it is
  not only one less dependency: the cookies that `signin()` sets
  (`.HarmonyAuth`, `.AUTHTOKEN`) have to be on the SAME jar as the catalog
  reads that follow, and sharing one client is what guarantees that.

A fourth literal, the content service's `Logitech-API-Key`, went out with the
code that used it -- see below.

## What this module takes, and why it is not the tokens any more

`GlobalCatalogClient` takes a `HarmonySession` -- what `ensure_session()`
returns -- and reads the bearer out of `session.tokens`. It used to take
`LipTokens`, which is what the OTHER entry point of `app/session.py` returned:
two functions a couple of letters apart in name and a whole type apart in
fact. Handing the obvious one to `catalogo.search()` failed
with `AttributeError: 'HarmonySession' object has no attribute
'access_token'`, three frames deep, on the press of the Search button. One
type crosses the seam now; see `app/session.py`'s docstring for the decision.

`signin()` still re-bootstraps on THIS client's jar. That is not a leftover:
the cookies `ensure_session()` collected live on the jar of the socket that
opened it, and every read below has to travel on the jar of the socket
making it.

## What was deliberately left behind

The original package could also create and delete devices on the account.
None of that came across. What is here is the four read-only operations the
Catalog screen actually calls:

    Discovery/GetJsonOperations         where the other three live
    DeviceManager/SearchGlobalDevices   search by manufacturer + model
    DeviceManager/GetGlobalDevices      the detail of one catalog device
    DeviceManager/GetGlobalLanguageCommands   its IR commands

Not here, on purpose: `GetCommands` (reads commands of devices ALREADY
registered on the account -- this app never registers any), the content
service (`content.dhg.myharmony.com`, which is what needed the API key), and
`backend_device()`, the builder of the payload that registers a device on the
account. `CatalogDeviceDefinition` did keep every one of its shape checks:
it is used to count the commands of a downloaded package, and dropping the
checks to keep the count would have turned a strict schema validation into a
`len()`.

## What this does NOT bring back

A catalog package carries each command's symbolic `KeyCode` and its
`ProtocolId`, and NOT the waveform -- `resources.ProtocolList` is not among
the four resources the catalog returns. `app/library.py` supplies the
timings from disk. This is the same limit the old bridge had; nothing here
made it better or worse.

Console check (no network, no account, no tokens):

    python3 app/catalog_client.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias
from urllib.parse import urlsplit, urlunsplit

from session import (
    AuthenticationError,
    HarmonySession,
    JsonClient,
    LipTokens,
    harmony_signin,
)

# ---------------------------------------------------------------------------
# Public addresses. Endpoints, not secrets: the only entry point that is
# hardcoded is discovery, and discovery is what returns the other 300.
# ---------------------------------------------------------------------------
DISCOVERY_URL = (
    "https://cf-svcs.myharmony.com/Discovery/Discovery.svc/json/GetJsonOperations"
)

#: The client type discovery is asked on behalf of. It is a protocol
#: constant, not a credential: it selects WHICH operation table comes back,
#: and the service answers it to anyone. Without it the answer is empty and
#: nothing below has an address to post to.
DISCOVERY_CLIENT_TYPE = "Redbull 1.0"

SEARCH_OPERATION = "DeviceManager/SearchGlobalDevices"
GLOBAL_DEVICES_OPERATION = "DeviceManager/GetGlobalDevices"
GLOBAL_LANGUAGE_COMMANDS_OPERATION = "DeviceManager/GetGlobalLanguageCommands"

#: `GetGlobalLanguageCommands` is missing from the operation table of some
#: accounts. The APK's own JavaScript builds it out of a sibling that IS
#: there -- same address, this name -- and so does `_post()`.
COMMANDS_OPERATION = "DeviceManager/GetCommands"

TIMEOUT = 30.0

#: The version of the offline package this module writes. `catalog.py` and
#: `library.py` both check it; it is not a decoration.
PACKAGE_SCHEMA = "0.2.0"

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
ScalarId: TypeAlias = int | str | None


class CatalogError(RuntimeError):
    """Catalog error that can be shown without leaking a credential."""


class ProtocolError(CatalogError):
    """The answer -- or the file on disk -- did not respect the contract."""


class ResourceError(CatalogError):
    """A catalog resource could not be fetched or read."""


# ---------------------------------------------------------------------------
# 1. discovery: the addresses of everything else
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Operation:
    """One entry of the operation table: what it is called, and where."""

    identifier: str
    address: str
    name: str

    def endpoint(self, *, json2: bool = False) -> str:
        address = cloudfront_address(self.address)
        if json2:
            address = address.replace("/json/", "/json2/")
        return address + self.name


def _swap_host(address: str, expected: set[str], new_host: str) -> str:
    parts = urlsplit(address)
    if parts.hostname not in expected:
        return address
    netloc = new_host
    if parts.port:
        netloc += ":%d" % parts.port
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def cloudfront_address(address: str) -> str:
    """`svcs` -> `cf-svcs`, the CDN front the client uses for discovery."""
    return _swap_host(address, {"svcs.myharmony.com"}, "cf-svcs.myharmony.com")


def load_balancer_address(address: str) -> str:
    """`svcs`/`cf-svcs` -> `lb-svcs`, the front that keeps session cookies.

    This is not cosmetic: the catalog reads only work while the cookies that
    `signin()` set are still being sent, and the CDN front does not carry
    them.
    """
    return _swap_host(
        address,
        {"svcs.myharmony.com", "cf-svcs.myharmony.com"},
        "lb-svcs.myharmony.com",
    )


def parse_operations(payload: JsonValue) -> dict[str, Operation]:
    """The operation table out of a `GetJsonOperations` answer."""
    if not isinstance(payload, dict):
        raise ProtocolError("GetJsonOperations did not return an object")
    items = payload.get("GetJsonOperationsResult")
    if not isinstance(items, list):
        raise ProtocolError("GetJsonOperationsResult is missing")
    operations: dict[str, Operation] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        identifier = item.get("Identifier")
        address = item.get("Address")
        name = item.get("Name")
        if not (isinstance(identifier, str) and identifier):
            continue
        if not (isinstance(address, str) and address):
            continue
        if not (isinstance(name, str) and name):
            continue
        operations[identifier] = Operation(identifier, address, name)
    if not operations:
        raise ProtocolError("discovery carried no usable operation")
    return operations


# ---------------------------------------------------------------------------
# 2. the client
# ---------------------------------------------------------------------------
def _unwrap(operation: Operation, payload: JsonValue) -> JsonValue:
    """Logitech wraps every answer in `<Name>Result` or in `Data`."""
    if not isinstance(payload, dict):
        return payload
    result_key = operation.name + "Result"
    if result_key in payload:
        return payload[result_key]
    if "Data" in payload:
        return payload["Data"]
    return payload


def _service_message(payload: JsonValue) -> str | None:
    if not isinstance(payload, dict):
        return None
    message = payload.get("Message")
    return message if isinstance(message, str) else None


class GlobalCatalogClient:
    """The catalog reads of the official client, and only those.

    Nothing here writes to the account. There is no method that creates,
    updates or deletes a device, because the four operations this class knows
    how to address cannot do it.
    """

    def __init__(
        self,
        session: HarmonySession,
        *,
        timeout: float = TIMEOUT,
        operations: dict[str, Operation] | None = None,
    ) -> None:
        self._session = session
        self._client = JsonClient(timeout=timeout)
        self._operations = dict(operations) if operations is not None else None

    @property
    def _bearer(self) -> str:
        """The `Authorization: Bearer` every read below travels with.

        Read through the session on purpose, in one place: if the type that
        crosses the seam ever changes again, this is the single line that
        stops compiling instead of four call sites that keep working until
        somebody presses a button.
        """
        return self._session.tokens.access_token

    def __enter__(self) -> GlobalCatalogClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Nothing to release: `JsonClient` holds no socket between calls.
        Kept so `with GlobalCatalogClient(...)` reads the same as before."""
        return None

    def signin(self) -> HarmonySession:
        """Harmony bootstrap: AccountId plus the `.HarmonyAuth`/`.AUTHTOKEN`
        cookies, on the jar every later read travels on. Same call
        `app/session.py` makes -- written once, not twice.

        The session it produces REPLACES the one this client was built with,
        because the new one is the one whose cookies are on this jar. The
        bearer is the same either way: it is the tokens that were validated.
        """
        self._session = harmony_signin(self._client, self._session.tokens)
        return self._session

    def operations(self) -> dict[str, Operation]:
        if self._operations is None:
            status, payload = self._client.post(
                DISCOVERY_URL,
                {"clientTypeId": DISCOVERY_CLIENT_TYPE},
                token=self._bearer,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "x-logitech-api-version": "1",
                },
            )
            if status != 200:
                raise ResourceError("discovery failed with HTTP %d" % status)
            self._operations = parse_operations(payload)
        return dict(self._operations)

    def _post(
        self,
        identifier: str,
        data: JsonValue,
        *,
        json2: bool = False,
        fallback_name: str | None = None,
    ) -> JsonValue:
        table = self.operations()
        operation = table.get(identifier)
        if operation is None and fallback_name:
            sibling = table.get(COMMANDS_OPERATION)
            if sibling is not None:
                operation = Operation(identifier, sibling.address, fallback_name)
        if operation is None:
            raise ProtocolError("unknown catalog operation: %s" % identifier)
        endpoint = load_balancer_address(operation.endpoint(json2=json2))
        status, payload = self._client.post(endpoint, data, token=self._bearer)
        message = _service_message(payload)
        if status == 400 and message and "Access is denied" in message:
            raise AuthenticationError(
                "Logitech refused the catalog session; no Hub will be used as "
                "a fallback"
            )
        if status != 200:
            raise ResourceError(
                "%s failed with HTTP %d%s"
                % (identifier, status, (": " + message) if message else "")
            )
        if payload is None:
            raise ResourceError("%s returned HTTP 200 without JSON" % identifier)
        return _unwrap(operation, payload)

    def search(
        self,
        manufacturer: str,
        model: str,
        *,
        device_type: int = 0,
        search_type: int = 2,
        max_results: int = 50,
    ) -> JsonValue:
        if max_results <= 0:
            raise ValueError("max_results has to be greater than zero")
        # Same class of guard as the line above, and for the same reason: the
        # service does not refuse an empty box, it answers `Matches: null`
        # (see `search_matches`). Stopping here means the screen gets one
        # sentence naming the empty box instead of a network round trip whose
        # answer is identical for every manufacturer.
        if not (manufacturer or "").strip():
            raise CatalogError(
                "the catalog searches by manufacturer AND model: the "
                "manufacturer is empty, and with it empty the catalog "
                "answers nothing at all, whatever the model says."
            )
        if not (model or "").strip():
            raise CatalogError(
                "the catalog searches by manufacturer AND model: the model "
                "is empty, and with it empty the catalog answers nothing at "
                "all, whatever the manufacturer says. Part of the model is "
                "enough -- `KDL` finds the Sony KDL-40S."
            )
        return self._post(
            SEARCH_OPERATION,
            {
                "manufacturer": manufacturer,
                "modelNumber": model,
                "deviceType": device_type,
                "searchType": search_type,
                "maxResults": max_results,
            },
            json2=True,
        )

    def global_devices(self, device_ids: list[ScalarId]) -> JsonValue:
        ids: list[JsonValue] = [i for i in device_ids if i is not None]
        if not ids:
            raise ValueError("at least one global device id is needed")
        return self._post(GLOBAL_DEVICES_OPERATION, ids, json2=True)

    def global_language_commands(self, version_id: ScalarId) -> JsonValue:
        if version_id is None:
            raise ValueError("the global language version id is missing")
        return self._post(
            GLOBAL_LANGUAGE_COMMANDS_OPERATION,
            {"globalLanguageVersionId": {"Value": version_id}},
            fallback_name="GetGlobalLanguageCommands",
        )


# ---------------------------------------------------------------------------
# 3. reading the answers
# ---------------------------------------------------------------------------
def search_matches(search_result: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(search_result, dict):
        raise ProtocolError("SearchGlobalDevices did not return an object")
    matches = search_result.get("Matches")
    # `Matches: null` is NOT a broken answer, and reporting it as one is what
    # made two different searches look like the same bug. MEASURED against
    # the live catalog: with either field empty the service answers
    # `{"Matches": null, "Status": 1}` -- same body for `Sony` with no model,
    # for `Panasonic` with no model, and for a model with no manufacturer.
    # It only searches with BOTH filled in (`Sony` + `KDL` -> 41 matches).
    # The old code turned all of those into one generic ProtocolError, so on
    # screen every brand gave the identical "the search failed", which reads
    # as "the catalog always answers the same thing".
    if matches is None:
        raise CatalogError(
            "Logitech's catalog did not search: it needs the manufacturer "
            "AND the model, and answers an empty result whenever either box "
            "is empty. Part of the model is enough (`KDL` finds the Sony "
            "KDL-40S)."
        )
    if not isinstance(matches, list):
        raise ProtocolError("SearchGlobalDevices did not return Matches")
    return [item for item in matches if isinstance(item, dict)]


def match_global_device_id(match: dict[str, JsonValue]) -> ScalarId:
    value = match.get("Id-")
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise ProtocolError("the selected result does not carry Id-")
    return value


def global_device_version_id(device: dict[str, JsonValue]) -> ScalarId:
    value = device.get("DefaultGlobalLanguageVersionId")
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise ProtocolError(
            "the global device does not carry DefaultGlobalLanguageVersionId"
        )
    return value


# ---------------------------------------------------------------------------
# 4. the offline package (0.2.0)
# ---------------------------------------------------------------------------
_SECRET_KEY = re.compile(
    r"(authorization|cookie|password|passwd|secret|access.?token|refresh.?token|"
    r"id.?token|auth.?token|challenge.?secret|hub.?key)",
    re.IGNORECASE,
)


def redact(value: JsonValue) -> JsonValue:
    """Replaces the VALUE of every key that names a credential.

    The package is written to disk and is meant to be readable and
    shareable, so what a capture could have dragged along -- a token, a
    cookie, an Authorization header -- does not get in. It filters by key
    name, not by content: a value nobody named cannot be recognised.
    """
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def canonical_sha256(value: JsonValue) -> str:
    """Hash of the VALUE, not of its formatting: sorted keys, no spaces. Two
    equal answers hash the same however they were serialised."""
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_offline_device_package(
    *,
    manufacturer: str,
    model: str,
    search_result: JsonValue,
    selected_match: JsonValue,
    global_device: JsonValue,
    language_commands: JsonValue,
) -> dict[str, JsonValue]:
    """The portable artifact: no account, no cookies, no tokens."""
    resources: dict[str, JsonValue] = {
        "search_result": redact(search_result),
        "selected_match": redact(selected_match),
        "global_device": redact(global_device),
        "global_language_commands": redact(language_commands),
    }
    return {
        "schema_version": PACKAGE_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "logitech-global-catalog-read-only",
        "query": {"manufacturer": manufacturer, "model": model},
        "resource_hashes": {
            name: canonical_sha256(value) for name, value in sorted(resources.items())
        },
        "resources": resources,
    }


def _object(value: JsonValue, *, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ProtocolError("%s does not contain an object" % context)
    return value


def _list(value: JsonValue, *, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ProtocolError("%s does not contain a list" % context)
    return value


def _scalar(value: JsonValue, *, context: str) -> int | str:
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise ProtocolError("%s does not contain an identifier" % context)
    return value


def _integer(value: JsonValue, *, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolError("%s does not contain an integer" % context)
    return value


def _text(value: JsonValue, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError("%s does not contain text" % context)
    return value


@dataclass(frozen=True, slots=True)
class CatalogDeviceDefinition:
    """A 0.2.0 package, read back with every field checked.

    `command_count()` in `catalog.py` only wants `expected_command_count`,
    and the temptation is to write that as a `len()` on one key. The checks
    are what make the number mean something: a package that lost
    `selected_match`, or that came from another schema, fails HERE with a
    name -- instead of reporting a plausible zero.
    """

    manufacturer: str
    model: str
    global_device_id: int | str
    global_language_version_id: int | str
    global_search_type: int
    device_type: int
    is_multi_code: bool
    capabilities: tuple[JsonValue, ...]
    expected_command_count: int

    @classmethod
    def from_package(cls, value: JsonValue) -> CatalogDeviceDefinition:
        package = _object(value, context="catalog package")
        if package.get("schema_version") != PACKAGE_SCHEMA:
            raise ProtocolError("a catalog package %s is required" % PACKAGE_SCHEMA)
        resources = _object(package.get("resources"), context="the package resources")
        match = _object(resources.get("selected_match"), context="selected_match")
        global_device = _object(resources.get("global_device"), context="global_device")
        commands = _list(
            resources.get("global_language_commands"),
            context="global_language_commands",
        )
        capabilities = _list(
            global_device.get("DeviceCapabilitiesWithPriority"),
            context="DeviceCapabilitiesWithPriority",
        )
        is_multi_code = match.get("IsMultiCode")
        if not isinstance(is_multi_code, bool):
            raise ProtocolError("selected_match does not carry IsMultiCode")
        return cls(
            manufacturer=_text(match.get("Manufacturer"), context="Manufacturer"),
            model=_text(match.get("DeviceModel"), context="DeviceModel"),
            global_device_id=_scalar(match.get("Id-"), context="the global Id-"),
            global_language_version_id=_scalar(
                match.get("GlobalLanguageVersionId-"),
                context="GlobalLanguageVersionId-",
            ),
            global_search_type=_integer(
                match.get("GlobalDeviceSearchType"), context="GlobalDeviceSearchType"
            ),
            device_type=_integer(match.get("DeviceType"), context="DeviceType"),
            is_multi_code=is_multi_code,
            capabilities=tuple(capabilities),
            expected_command_count=len(commands),
        )


# ---------------------------------------------------------------------------
# 5. disk
# ---------------------------------------------------------------------------
def atomic_write_json(path: Path, value: JsonValue, *, private: bool = True) -> None:
    """Writes JSON so that a half-written file is never observable: a
    temporary file in the same directory, created with its final mode from
    the first byte, and `os.replace` at the end."""
    parent = path.parent
    existed = parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix" and not existed:
        parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent, prefix="." + path.name + ".", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        mode = 0o600 if private else 0o644
        if os.name == "posix":
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            path.chmod(mode)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> JsonValue:
    """UTF-8 JSON off disk. `json` cannot decode anything that is not already
    a JSON value, so there is nothing left to validate afterwards."""
    return json.loads(path.expanduser().read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# console check -- no network, no account, no tokens
# ---------------------------------------------------------------------------
_FAKE_MATCH: dict[str, JsonValue] = {
    "Manufacturer": "Acme",
    "DeviceModel": "X-1",
    "Id-": 4242,
    "GlobalLanguageVersionId-": 99,
    "GlobalDeviceSearchType": 2,
    "DeviceType": 1,
    "IsMultiCode": False,
}


def _self_check() -> int:
    """What can be measured with no account: that the hosts get rewritten,
    that the package redacts, hashes and reads back, and that nothing in this
    module needs a third-party import."""
    import ast  # noqa: PLC0415 -- only the self-check parses this file
    import sys  # noqa: PLC0415

    failures: list[str] = []

    # -- the two host rewrites, including the one that must NOT happen ------
    front = cloudfront_address("https://svcs.myharmony.com/x/Y.svc/json/")
    balanced = load_balancer_address(front)
    untouched = load_balancer_address("https://accounts.logi.com/identity/signin")
    print("discovery front : %s" % front)
    print("cookie front    : %s" % balanced)
    if "cf-svcs.myharmony.com" not in front:
        failures.append("cloudfront_address did not rewrite the host")
    if "lb-svcs.myharmony.com" not in balanced:
        failures.append("load_balancer_address did not rewrite the host")
    if untouched != "https://accounts.logi.com/identity/signin":
        failures.append("load_balancer_address touched a host that is not Logitech's")

    operation = Operation("x", "https://svcs.myharmony.com/Dm/Dm.svc/json/", "Search")
    if operation.endpoint(json2=True) != (
        "https://cf-svcs.myharmony.com/Dm/Dm.svc/json2/Search"
    ):
        failures.append("Operation.endpoint did not build the json2 address")

    # -- the operation table -----------------------------------------------
    table = parse_operations(
        {
            "GetJsonOperationsResult": [
                {
                    "Identifier": SEARCH_OPERATION,
                    "Address": "https://a/b/",
                    "Name": "S",
                },
                {"Identifier": "", "Address": "https://a/b/", "Name": "S"},  # dropped
                "not an object",  # dropped
            ]
        }
    )
    print("operations parsed: %d" % len(table))
    if list(table) != [SEARCH_OPERATION]:
        failures.append("parse_operations did not drop the unusable entries")

    # -- redaction: by key name, everywhere, at any depth -------------------
    dirty: JsonValue = {
        "Name": "Power",
        "access_token": "SECRET",
        "inner": [{"Cookie": "SECRET", "Model": "X-1"}],
    }
    clean = redact(dirty)
    if "SECRET" in json.dumps(clean):
        failures.append("redact() let a credential through")
    print("redaction       : %s" % json.dumps(clean, sort_keys=True))

    # -- the package: schema, hashes, and reading it back -------------------
    package = build_offline_device_package(
        manufacturer="Acme",
        model="X-1",
        search_result={"Matches": [_FAKE_MATCH], "access_token": "SECRET"},
        selected_match=_FAKE_MATCH,
        global_device={"DeviceCapabilitiesWithPriority": [1, 2, 3]},
        language_commands=[{"Name": "Power"}, {"Name": "Volume"}],
    )
    if "SECRET" in json.dumps(package):
        failures.append("the package carried a token to disk")
    resources = package["resources"]
    hashes = package["resource_hashes"]
    assert isinstance(resources, dict) and isinstance(hashes, dict)
    for name, value in resources.items():
        if hashes.get(name) != canonical_sha256(value):
            failures.append("the hash of %s does not match its resource" % name)
    definition = CatalogDeviceDefinition.from_package(package)
    print(
        "package         : %s %s, %d command(s), %d capability(ies)"
        % (
            definition.manufacturer,
            definition.model,
            definition.expected_command_count,
            len(definition.capabilities),
        )
    )
    if definition.expected_command_count != 2:
        failures.append("the command count came out wrong")

    # And a package of another schema has to be rejected, not counted.
    try:
        CatalogDeviceDefinition.from_package({**package, "schema_version": "0.3.0"})
        failures.append("a 0.3.0 package was accepted as a catalog package")
    except ProtocolError as error:
        print("wrong schema    -> %s" % type(error).__name__)
        if PACKAGE_SCHEMA not in str(error):
            failures.append("the rejection does not name the schema it wanted")

    # -- disk: round trip and mode -----------------------------------------
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "new" / "package.json"
        atomic_write_json(target, package, private=False)
        mode = target.stat().st_mode & 0o777
        print("file mode       : %o" % mode)
        if os.name == "posix" and mode != 0o644:
            failures.append("the package came out %o, not 644" % mode)
        if canonical_sha256(read_json(target)) != canonical_sha256(package):
            failures.append("the package did not round-trip through disk")

    # -- THE SEAM, walked end to end with the network stubbed --------------
    # This is the check that was missing. `app/session.py` and this file were
    # rewritten in parallel and each one's console check passed on its own,
    # while together they raised `AttributeError: 'HarmonySession' object
    # has no attribute 'access_token'` the moment somebody pressed Search.
    # Nothing here reaches the network: `JsonClient.post` is replaced by a
    # canned responder, and what is measured is the TYPE that travels --
    # what `ensure_session()` returns going into `GlobalCatalogClient` and
    # coming out the other side as results, plus the bearer that was
    # actually sent on every request.
    import session  # noqa: PLC0415 -- only the self-check drives a login

    bearers: list[str | None] = []
    search_endpoint = "/json2/SearchGlobalDevices"

    def canned(_self, url, _payload, *, token=None, headers=None):  # noqa: ANN001
        bearers.append(token)
        if url == session.HARMONY_SIGNIN_URL:
            return 200, {"AccountId": "acc-1", "AuthToken": "auth-1", "Email": None}
        if url == DISCOVERY_URL:
            return 200, {
                "GetJsonOperationsResult": [
                    {
                        "Identifier": SEARCH_OPERATION,
                        "Address": "https://svcs.myharmony.com/Dm/Dm.svc/json/",
                        "Name": "SearchGlobalDevices",
                    }
                ]
            }
        if url.endswith(search_endpoint):
            return 200, {"SearchGlobalDevicesResult": {"Matches": [_FAKE_MATCH]}}
        return 404, None

    original_post = session.JsonClient.post
    session.JsonClient.post = canned  # type: ignore[method-assign]
    try:
        with tempfile.TemporaryDirectory() as scratch:
            token_file = Path(scratch) / "token.json"
            session.save_lip_tokens(
                LipTokens(access_token="AAA", id_token="III", refresh_token="RRR"),
                token_file,
            )
            opened = session.ensure_session("someone@example.com", token_file=token_file)
            with GlobalCatalogClient(opened) as client:
                client.signin()
                found = search_matches(client.search("Acme", "X-1"))
    finally:
        session.JsonClient.post = original_post  # type: ignore[method-assign]

    print(
        "seam            : %s -> %d match(es), bearer sent %d time(s)"
        % (type(opened).__name__, len(found), len(bearers))
    )
    if type(opened).__name__ != HarmonySession.__name__:
        failures.append("ensure_session() did not return the type this client takes")
    if len(found) != 1 or found[0].get("Manufacturer") != "Acme":
        failures.append("the seam walk did not come back with the search results")
    if set(bearers) != {"AAA"}:
        failures.append(
            "a request went out with the wrong bearer: %r"
            % sorted(set(str(b) for b in bearers))
        )

    # -- the point of the whole file: no third-party import ----------------
    # The rule, stated without naming anybody: everything this file
    # imports is either the standard library or a module of this same app.
    # Deriving the sibling names instead of writing them down is not
    # fastidiousness -- the export renames the app's modules, and a literal
    # here would keep pointing at the old name and turn this check green
    # while measuring nothing.
    siblings = {p.stem for p in Path(__file__).parent.glob("*.py")}
    allowed = set(sys.stdlib_module_names) | siblings
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    outside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            outside |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            outside.add(node.module.split(".")[0])
    third_party = sorted(outside - allowed)
    print("imports         : %s" % ", ".join(sorted(outside)))
    if third_party:
        failures.append("third-party import(s): %s" % ", ".join(third_party))

    if failures:
        print("CATALOG CLIENT: FAILED")
        for failure in failures:
            print("  -", failure)
        return 1
    print("CATALOG CLIENT: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
