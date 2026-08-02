"""Logitech's public catalog -- this module does not create or delete
anything in any account, and does not read any Logitech device identity
(`remoteId`/`skinId`/`hubSecret`).

Two data paths that should not be confused:

1. **Catalog package** (`schema_version: "0.2.0"`, what
   `build_offline_device_package` builds): search + global_devices +
   global_language_commands, against Logitech's public catalog -- a normal
   account login (LIP), but WITHOUT creating or touching any device.
   `command_count()` reads its command count by reusing
   `CatalogDeviceDefinition.from_package` from `app/catalog_client.py` --
   it does not reimplement the count. `save_device_package()` persists it to
   disk with that same module's `atomic_write_json`.

   HEADS UP -- what this package does NOT bring: `resources.ProtocolList`
   (the timing definition of each protocol: carrier, mark/space duration per
   bit). Every command arrives with its symbolic `KeyCode` (protocol +
   payload, e.g. `"G:Magnavox 13 Bit:()(0x07FF)():3"`) and its `ProtocolId`,
   but not the waveform. Measured, not assumed: take any device, download
   its catalog package, and put it side by side with a full export of that
   SAME device -- the `resources` keys of the package are
   `global_device/global_language_commands/search_result/selected_match`,
   and there is no `ProtocolList` among them at all. `app/library.py`
   supplies that part, with the protocols already on disk; the package
   alone is not enough.

2. **File with protocols** (`schema_version: "0.3.0"`,
   `<folder>/hub-config-with-device.json`). The ones already on disk come
   from a time when a temporary sign-up on a live account was in fact
   needed to get `resources.ProtocolList`; that step was removed from the
   flow. Today the new files are written by `app/library.py` (catalog +
   protocols already on disk) and `app/ir_manual.py` (a manually imported
   `.ir`), **neither of which touches any account**. `read_local_export()`
   only READS -- no network, no tokens -- counting the requested device's
   commands straight from `resources.DeviceList`, without trusting the
   `validation.commands` that the same file already wrote in its own
   `manifest.json` (that would be a circular check).

## The client is IN this repository now

Path 1 used to need a separate package that was never published, and in a
clone the Search button raised on the first press -- while this module
imported perfectly, so every gate that measured imports called the app
complete. That is exactly why "it imports" is not a measure of "it works".
The client lives here now, rewritten on the standard library:
`app/catalog_client.py`. Nothing of Logitech's travels in it (no
`client_id`, no impersonating `User-Agent`, no API key), and it dropped
`httpx` on the way.

## Half of this module still runs with nothing installed at all

The two paths do not need the same things, and keeping that split is worth
the eight lines it costs.

Path 1 signs in, so it reaches `app/session.py` and through it `keyring` --
the one third-party import left in the app's account path. Path 2 reads a
file that is already sitting on disk: no account, no network, no tokens,
nothing but `json`. Tying path 2's fate to path 1's dependency cost the
whole module, and the cost was measured: with the import blocked, importing
this module raised, `api.catalog_local()` reported `commands=None` on every
device already on disk, and the Devices screen had no count to show. Same
run with the import guarded: `commands=63`, the number read out of the file
itself.

So the import is guarded, and the split is by what each function ACTUALLY
touches:

* `read_local_export()` works always. It is the only function here that
  reads no account and opens no socket, and it is the one `app/api.py`
  calls to fill in the command count of every device already on disk.
* `search()`, `fetch_device_package()`, `command_count()` and
  `save_device_package()` raise `CatalogUnavailable` if that import failed.
  Today the only way it can fail is a dependency the app declares and
  `uv sync` installs, and the message says so and says the command --
  unlike the old one, which named a package nobody could obtain.

`app/api.py` already guards every one of those four call sites and reports
which optional modules did not import.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# 0. The account half, guarded
#
# `CLIENT_MISSING` is None when the import worked and carries the error
# when it did not. It is a plain module attribute on purpose: whoever wants
# to know can read it without triggering anything, and the four functions
# that need the network ask `_require_client()` at their first line so the
# failure lands where the call was made and not three frames in with a
# NameError.
# --------------------------------------------------------------------------
try:
    from catalog_client import (
        CatalogDeviceDefinition,
        CatalogError,
        GlobalCatalogClient,
        JsonValue,
        ProtocolError,
        atomic_write_json,
        build_offline_device_package,
        global_device_version_id,
        match_global_device_id,
        read_json,
        search_matches,
    )
    from session import HarmonySession

    CLIENT_MISSING: str | None = None
except ImportError as _exc:  # a dependency of the app is not installed
    CLIENT_MISSING = "%s: %s" % (type(_exc).__name__, _exc)

    #: Only ever used in annotations, and `from __future__ import
    #: annotations` keeps those unevaluated: there is no session to type
    #: when there is nothing to log into.
    HarmonySession = Any
    JsonValue = Any

    class CatalogError(RuntimeError):
        """Same name and same base as the one in `catalog_client.py`, so
        `except` clauses written against either one keep catching this
        module's errors."""

    class ProtocolError(CatalogError):
        """A file did not respect the shape this module expects."""

    def read_json(path: Path) -> JsonValue:
        """What `read_json` in `catalog_client.py` does: decode UTF-8 JSON
        off disk. Repeated here, and only here, so that reading a device
        folder already on disk survives an app installed halfway."""
        return json.loads(path.expanduser().read_text(encoding="utf-8"))


class CatalogUnavailable(CatalogError):
    """Raised by the four functions that cannot work offline."""


def _require_client(what: str) -> None:
    """Guard for everything that talks to Logitech. Says what is missing,
    and -- because a user reading an error wants a way forward, not a
    diagnosis -- what still works without it."""
    if CLIENT_MISSING is None:
        return
    raise CatalogUnavailable(
        "%s needs `catalog_client.py`, the read-only client of Logitech's "
        "catalog, and importing it failed (%s). That module IS part of this "
        "repository, so what is missing is a dependency of the app -- in "
        "practice `keyring`, which `app/session.py` needs to reach the "
        "keychain. Install it with:\n"
        "    cd app && uv sync\n"
        "What still works meanwhile, with no account at all: importing an "
        "`.ir` by hand (`app/ir_manual.py`), joining it with the protocols "
        "already on disk (`app/library.py`), and reading any device "
        "folder already exported (`read_local_export()`, right here)."
        % (what, CLIENT_MISSING)
    )


# --------------------------------------------------------------------------
# 1. Search the global catalog and build the offline package (0.2.0)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CatalogMatch:
    index: int
    manufacturer: str
    model: str
    display_text: str
    raw: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class SearchResult:
    raw: JsonValue  # needed for build_offline_device_package later
    matches: list[CatalogMatch]


def search(
    session: HarmonySession,
    manufacturer: str,
    model: str,
    *,
    max_results: int = 50,
) -> SearchResult:
    """Searches Logitech's global catalog. `session` must already come open
    from `ensure_session()` in `app/session.py` -- this layer does not retry.

    It is the SESSION that travels, not the tokens. The two used to be
    interchangeable at this signature and they are not: see
    `app/session.py`'s docstring.
    """
    _require_client("searching Logitech's catalog")
    with GlobalCatalogClient(session) as client:
        client.signin()
        raw = client.search(manufacturer, model, max_results=max_results)
    parsed = search_matches(raw)
    matches = [
        CatalogMatch(
            index=i,
            manufacturer=str(m.get("Manufacturer", "")),
            model=str(m.get("DeviceModel", "")),
            display_text=str(m.get("DisplayText") or m.get("SelectedText") or ""),
            raw=m,
        )
        for i, m in enumerate(parsed)
    ]
    return SearchResult(raw=raw, matches=matches)


def fetch_device_package(
    session: HarmonySession,
    manufacturer: str,
    model: str,
    result: SearchResult,
    match_index: int,
) -> dict[str, JsonValue]:
    """Repeats `cli.py`'s `catalog-fetch`: detail + IR commands, offline
    package 0.2.0. Takes the same `HarmonySession` as `search()`."""
    _require_client("downloading a device from Logitech's catalog")
    if match_index < 0 or match_index >= len(result.matches):
        raise CatalogError(f"match_index out of range; there are {len(result.matches)}")
    selected = result.matches[match_index].raw
    global_id = match_global_device_id(selected)
    with GlobalCatalogClient(session) as client:
        client.signin()
        global_result = client.global_devices([global_id])
        if not isinstance(global_result, list) or not global_result:
            raise CatalogError("GetGlobalDevices did not return the device")
        global_device = global_result[0]
        if not isinstance(global_device, dict):
            raise CatalogError("GetGlobalDevices returned an unexpected format")
        version_id = global_device_version_id(global_device)
        language_commands = client.global_language_commands(version_id)
    return build_offline_device_package(
        manufacturer=manufacturer,
        model=model,
        search_result=result.raw,
        selected_match=selected,
        global_device=global_device,
        language_commands=language_commands,
    )


def command_count(package: JsonValue) -> int:
    """How many commands a just-downloaded catalog package (0.2.0) carries.
    Reuses the schema validation of `catalog_client.py` instead of writing
    it: rejects with ProtocolError anything that isn't schema_version 0.2.0.

    That reuse is why this one does NOT degrade: writing the count here by
    hand would mean a second copy of the 0.2.0 check, free to drift from
    the one that produced the package. Its caller in `app/api.py` already
    treats the number as optional -- the readiness verdict of a pending
    package comes from `materialize()` in `app/library.py`, not from
    this."""
    _require_client("counting the commands of a downloaded catalog package")
    return CatalogDeviceDefinition.from_package(package).expected_command_count


# --------------------------------------------------------------------------
# 2. Save the catalog package (0.2.0) to disk -- no account, no Hub
# --------------------------------------------------------------------------


def save_device_package(package: JsonValue, output: Path) -> Path:
    """Writes the catalog package to disk with `atomic_write_json(...,
    private=False)` -- a temporary file in the same directory and an
    `os.replace` at the end, so a half-written package is never observable.
    Nothing is created or deleted in any account: this only serializes what
    `fetch_device_package()` already fetched over read-only HTTP against the
    public catalog."""
    _require_client("saving a downloaded catalog package")
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output, package, private=False)
    return output


# --------------------------------------------------------------------------
# 3. Read a hub-config export already saved to disk
#
# No network and no tokens: everything below runs on a clone where not even
# `keyring` got installed, which is why it sits outside the guard.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LocalExportSummary:
    """Summary of a `hub-config-with-device.json` already on disk (written
    either by `write()` in `app/library.py`, `import_device()` in
    `app/ir_manual.py`, or an older export of the account). Used by Catalog
    and Control to count commands without hitting the network again."""

    manufacturer: str
    model: str
    command_count: int
    directory: Path


def _require_object(value: JsonValue, *, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{context} does not contain an object")
    return value


def _require_list(value: JsonValue, *, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ProtocolError(f"{context} does not contain a list")
    return value


def _require_text(value: JsonValue, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{context} does not contain text")
    return value


def read_local_export(directory: Path) -> LocalExportSummary:
    """Parses `<directory>/manifest.json` + `hub-config-with-device.json` --
    a file ALREADY ON DISK from an earlier run, with the protocols already
    captured -- and returns how many commands the requested device has.

    Opens no network, requires no tokens (the file is already there): it
    counts `Commands` again, straight from
    `resources.DeviceList.DevicesWithFeatures`, matching by
    Manufacturer+Model against the manifest's `requested_device`, instead of
    trusting `manifest.json.validation` (which was already computed by the
    same code that generated the file -- that would be a circular check).
    """
    manifest = _require_object(
        read_json(directory / "manifest.json"),
        context="manifest.json",
    )
    requested = _require_object(
        manifest.get("requested_device"),
        context="manifest.json.requested_device",
    )
    manufacturer = _require_text(
        requested.get("manufacturer"),
        context="requested_device.manufacturer",
    )
    model = _require_text(requested.get("model"), context="requested_device.model")

    snapshot = _require_object(
        read_json(directory / "hub-config-with-device.json"),
        context="hub-config-with-device.json",
    )
    resources = _require_object(snapshot.get("resources"), context="resources")
    device_list = _require_object(resources.get("DeviceList"), context="DeviceList")
    entries = _require_list(
        device_list.get("DevicesWithFeatures"),
        context="DeviceList.DevicesWithFeatures",
    )

    matches = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        device = entry.get("Device")
        if not isinstance(device, dict):
            continue
        if device.get("Manufacturer") == manufacturer and device.get("Model") == model:
            matches.append(entry)

    if len(matches) != 1:
        raise ProtocolError(
            f"expected exactly 1 device {manufacturer!r} {model!r} in "
            f"DeviceList, found {len(matches)}"
        )

    commands = _require_list(
        matches[0].get("Commands"), context="the device's Commands"
    )
    return LocalExportSummary(
        manufacturer=manufacturer,
        model=model,
        command_count=len(commands),
        directory=directory,
    )
