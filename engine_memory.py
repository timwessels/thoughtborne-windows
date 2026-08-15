"""Memory of the engine the user last selected, #193 (see DECISIONS.md D-008).

A successful engine switch is recorded in a small machine-written state file
beside the project files, so the next start opens on it instead of always on the
built-in default. Only a deliberate switch is ever recorded -- never the startup
carousel's fall-through, which is an outage rather than a choice.

The state file is NOT a settings file: `personal_settings.json` stays
user-authored and `settings_io.py` stays its only writer (D-002). An explicit
`defaults.api` (#55) therefore outranks the memory; `resolve_startup_engine` is
that precedence rule, kept pure so it is decided in exactly one place, and
`carousel_from` is the startup fall-through order that follows from it.

Reading and writing are best-effort: a missing, unreadable, corrupt, or stale
file resolves to None and the normal default chain applies, and a failed write
costs the memory but never the switch that triggered it (the retry-marker
contract, `audio_handler.write_retry_marker`).

Pure/stdlib so it imports and is tested off Windows -- see `test_engine_memory.py`.
"""
import os
import json
import tempfile
from pathlib import Path

STATE_FILENAME = "runtime_state.json"

# Re-emitted from here on every write, so it cannot drift: a user who finds this
# file in the install folder can tell what wrote it, and that deleting it costs
# nothing. Mirrors the personal_settings.example.json house style.
STATE_COMMENT = (
    "Written by Thoughtborne itself -- not a settings file. It records the engine "
    "you last selected with the switch hotkey, so the next start opens on it. Safe "
    "to delete; it is recreated on the next switch. Your own settings live in "
    "personal_settings.json, whose 'defaults.api' outranks this file."
)

_LAST_ENGINE_KEY = "last_engine"


def state_path(base_dir):
    """The state file beside the project files (config.SCRIPT_DIR in production)."""
    return Path(base_dir) / STATE_FILENAME


def read_last_engine(path, valid):
    """The remembered engine, or None when there is nothing trustworthy to return.

    None covers every failure alike -- no file (the common first-start case),
    unreadable, corrupt JSON, the wrong shape, or a value outside `valid` (an
    engine id retired or renamed by a later release). `valid` is passed in rather
    than imported so this module keeps no project imports; the caller hands it
    config.AVAILABLE_APIS, the same whitelist defaults.api is checked against.

    Never raises, and never repairs or deletes the file: the next successful
    write replaces it atomically, and a file we did not understand is not ours to
    throw away.

    The catch is deliberately as broad as the write side's: the expected failures
    are OSError and ValueError (JSONDecodeError and a non-UTF-8 read are both
    ValueError subclasses), but a hostile file can also drive json into a
    RecursionError, and this is called from ThoughtborneApp.__init__ where any
    escape aborts the whole start. Nothing here is worth a failed start.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    api = data.get(_LAST_ENGINE_KEY)
    return api if isinstance(api, str) and api in valid else None


def write_last_engine(path, api, valid) -> bool:
    """Record `api` as the last selected engine. Returns True only when the file
    was actually replaced.

    Validated against `valid` on this side too, so junk can never enter the file
    in the first place. Written atomically -- temp file in the same directory,
    then os.replace -- so a crash or a second instance can never leave a
    half-written state file behind. Best-effort and never raises: an unwritable
    install directory or a locked file just leaves the memory as it was.
    """
    if not (isinstance(api, str) and api in valid):
        return False
    path = Path(path)
    payload = json.dumps({"_comment": STATE_COMMENT, _LAST_ENGINE_KEY: api},
                         indent=2) + "\n"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                                   suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(payload)
        os.replace(tmp, str(path))
        return True
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def resolve_startup_engine(*, remembered, configured, builtin_default):
    """The engine the startup carousel should begin at, plus where it came from:
    (api, source) with source one of 'config' / 'memory' / 'default'.

    D-008: an explicit, valid `defaults.api` wins -- deliberate configuration set
    through a visible control outranks a memory the user never sees. The memory
    fills the gap where nothing is configured, which is where #193's users live.
    `configured` is the accepted defaults.api value or None; presence decides, not
    difference from the built-in default, so a hand-written pin ON the built-in
    default is honored like any other.
    """
    if configured:
        return configured, "config"
    if remembered:
        return remembered, "memory"
    return builtin_default, "default"


def carousel_from(start, apis):
    """The startup carousel order beginning at `start`: the full `apis` rotation,
    so every engine is still tried exactly once and a remembered engine can never
    shorten the fall-through path (#40).

    `start` outside `apis` yields `[start] + apis` instead -- only reachable via a
    hand-edited `config.DEFAULT_API` (a remembered engine is whitelist-validated on
    read). Trying it first anyway makes the transcriber factory's "Unknown API"
    error surface as a skip line naming it, rather than swallowing the typo.
    """
    apis = list(apis)
    try:
        i = apis.index(start)
    except ValueError:
        return [start] + apis
    return [apis[(i + step) % len(apis)] for step in range(len(apis))]
