"""
Hotkey Manager Module - Win32 RegisterHotKey API

Replaces keyboard.add_hotkey() with the Win32 RegisterHotKey API.
RegisterHotKey survives Connected Standby (sleep/wake), unlike the
WH_KEYBOARD_LL hook used by the keyboard library.

Also provides is_key_pressed() using GetAsyncKeyState as a hook-free
replacement for keyboard.is_pressed().

Public API:
    HotkeyManager:
        register(hotkey_str, callback, name="") -> int
        start() -> bool
        suspend() -> bool     # release every registration (#335)
        resume() -> bool      # build them up again
        stop()

    is_key_pressed(key_name: str) -> bool
"""

import ctypes
import ctypes.wintypes
import logging
import threading

# The modifier flags, the modifier map, the static VK map (the whole bindable key set)
# and the structural parser live in the pure, ctypes-free hotkey_parse module (#55)
# so config can share the exact same lexical layer without importing this
# Windows-bound module. Re-exported here (imported names) so any consumer of
# hotkey_manager.MOD_* / MODIFIER_MAP / VK_MAP keeps working unchanged.
from hotkey_parse import (
    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT,
    MODIFIER_MAP, VK_MAP, HotkeyParseError, parse_hotkey_lexical,
)

# The module's public surface, spelled out so the re-exports above read as the
# deliberate API they are rather than as oversights. Nothing in the tree does
# `from hotkey_manager import *`, so this changes no behaviour -- it states the
# intent in a form a tool can read, and the undefined-name lane (#289) verifies
# from here on that every name listed actually exists.
__all__ = [
    # this module's own API
    "HotkeyManager", "is_key_pressed", "is_vk_pressed", "VK_RMENU",
    # re-exported from hotkey_parse (#55) so hotkey_manager.X keeps working
    "MOD_ALT", "MOD_CONTROL", "MOD_SHIFT", "MOD_WIN", "MOD_NOREPEAT",
    "MODIFIER_MAP", "VK_MAP", "HotkeyParseError", "parse_hotkey_lexical",
]

logger = logging.getLogger('Thoughtborne.HotkeyManager')

# ===== Win32 Constants =====
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

# Two private messages for the listener thread (#335). Un- and re-registering are
# thread-affine -- RegisterHotKey(NULL, ...) binds the combo to the CALLING thread,
# so only the listener may release and rebuild its own registrations -- and the way
# to reach that thread is the one stop() already uses: post into its message queue.
# The WM_APP range is reserved for exactly this kind of application-private message.
WM_APP = 0x8000
WM_APP_SUSPEND_HOTKEYS = WM_APP + 1
WM_APP_RESUME_HOTKEYS = WM_APP + 2

# ===== Win32 Functions =====
# user32 via a private WinDLL(use_last_error=True): ctypes then captures the Win32
# LastError after each call, so the RegisterHotKey failure path can read the real
# code via get_last_error() -- 1409 "already registered by another application"
# instead of a stale 0 (#165). It also de-shares our argtypes/restype from the
# ctypes.windll cache that keyboard/pyperclip/pyautogui mutate.
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.windll.kernel32

RegisterHotKey = user32.RegisterHotKey
RegisterHotKey.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.wintypes.UINT, ctypes.wintypes.UINT]
RegisterHotKey.restype = ctypes.wintypes.BOOL

UnregisterHotKey = user32.UnregisterHotKey
UnregisterHotKey.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
UnregisterHotKey.restype = ctypes.wintypes.BOOL

GetMessageW = user32.GetMessageW
PostThreadMessageW = user32.PostThreadMessageW
GetAsyncKeyState = user32.GetAsyncKeyState
GetAsyncKeyState.argtypes = [ctypes.c_int]
GetAsyncKeyState.restype = ctypes.c_short

GetCurrentThreadId = kernel32.GetCurrentThreadId

# ===== VK Code Maps =====
# MODIFIER_MAP and VK_MAP (the whole static key set) come from hotkey_parse (#55).

# Modifier string -> VK code (for GetAsyncKeyState)
VK_KEY_MAP = {
    'ctrl': 0x11,      # VK_CONTROL
    'control': 0x11,
    'alt': 0x12,        # VK_MENU
    'shift': 0x10,      # VK_SHIFT
    'win': 0x5B,        # VK_LWIN
    'windows': 0x5B,
}
# Merge every VK_MAP code into VK_KEY_MAP for is_key_pressed. The whole static key
# set rides along automatically, so is_key_pressed('f9') -- or 'home', 'num0' -- works
# off the one table, with no second list to keep in step.
VK_KEY_MAP.update(VK_MAP)

# Side-specific VK codes for the push-to-talk detector (#66). The name-keyed
# VK_KEY_MAP above maps 'ctrl' to the COMBINED VK_CONTROL (0x11) on purpose and
# cannot tell left from right; PTT needs Left-Ctrl specifically as its trigger
# and Right-Alt specifically as the AltGr discriminator, so these are read raw
# via is_vk_pressed() instead of going through is_key_pressed().
VK_LCONTROL = 0xA2   # left Ctrl, distinct from combined VK_CONTROL (0x11)
VK_RMENU = 0xA5      # right Alt = AltGr discriminator on German QWERTZ


def is_vk_pressed(vk: int) -> bool:
    """
    Check whether a raw virtual-key code is currently pressed, by VK number.

    High-bit (physical-down) test via GetAsyncKeyState, the same hook-free
    primitive is_key_pressed() uses (survives sleep/wake). Unlike
    is_key_pressed(), this is keyed by raw VK code, so it can distinguish keys
    the name map deliberately collapses (left vs. right Ctrl, Right-Alt). Used
    by the PTT detector.

    Args:
        vk: Virtual-key code (e.g. VK_LCONTROL = 0xA2)

    Returns:
        True if the key is currently physically down
    """
    return bool(GetAsyncKeyState(vk) & 0x8000)


def _resolve_vk_code(key_str: str) -> int:
    """
    Resolve a key string to a VK code via the static VK_MAP -- the only key
    lane since D-023 removed the layout-resolved one.

    Args:
        key_str: Key name (e.g. 'w', '4', 'f9')

    Returns:
        VK code as integer

    Raises:
        ValueError: If the key is not in VK_MAP. Config-time validation
            (classify_key) rejects such keys before registration, so this
            guards hand-edited defaults rather than a normal path.
    """
    try:
        return VK_MAP[key_str]
    except KeyError:
        raise ValueError(f"Cannot resolve key '{key_str}' to VK code") from None


def _parse_hotkey(hotkey_str: str) -> tuple:
    """
    Parse a hotkey string like 'ctrl+alt+w' into (modifiers, vk_code).

    Thin wrapper: the pure structural split (modifier/key validation, always
    setting MOD_NOREPEAT to prevent repeat when a key is held) lives in
    hotkey_parse.parse_hotkey_lexical (#55); the VK lookup (_resolve_vk_code)
    stays here.

    Args:
        hotkey_str: Hotkey string (e.g. 'ctrl+alt+w', 'ctrl+alt+4', 'f9')

    Returns:
        Tuple of (modifier_flags, vk_code)

    Raises:
        ValueError: If parsing fails (HotkeyParseError is a ValueError, so the
            registration `except ValueError` catches structural failures too).
    """
    modifiers, key_part = parse_hotkey_lexical(hotkey_str)
    return (modifiers, _resolve_vk_code(key_part))


def is_key_pressed(key_name: str) -> bool:
    """
    Check if a key is currently pressed using GetAsyncKeyState.

    Hook-free replacement for keyboard.is_pressed(). Reads the physical
    key state directly from the hardware, survives sleep/wake cycles.

    Args:
        key_name: Key name (e.g. 'ctrl', 'alt', 'shift', 'a', 'w')

    Returns:
        True if key is currently pressed
    """
    key_lower = key_name.lower()
    vk = VK_KEY_MAP.get(key_lower)
    if vk is None:
        logger.warning(f"is_key_pressed: unknown key '{key_name}'")
        return False

    state = GetAsyncKeyState(vk)
    return bool(state & 0x8000)


class HotkeyManager:
    """
    Manages global hotkeys using the Win32 RegisterHotKey API.

    Usage:
        hm = HotkeyManager()
        hm.register('ctrl+alt+w', my_callback, name='start_recording')
        hm.start()   # Starts listener thread, blocks until registration done
        ...
        hm.stop()    # Clean shutdown
    """

    def __init__(self):
        self._registrations = []  # List of (hotkey_id, hotkey_str, callback, name)
        self._hotkey_map = {}     # hotkey_id -> (callback, name)
        self._thread = None
        self._thread_id = None    # Win32 thread ID for PostThreadMessageW
        self._started = threading.Event()
        self._next_id = 1
        # The two hooks of the #335 suspend, set by the owner after construction and
        # called ON THE LISTENER THREAD -- which is what lets the app's ACK file state
        # a fact rather than an intention. Both sit on the safe side of their edge:
        # `on_suspended` runs once the registrations are really gone, `on_resuming` as
        # the rebuild begins, before any combo can fire again. So the ACK the owner
        # writes and removes in them can be late, never wrong: it exists only while
        # the hotkeys really are released. Callbacks instead of a file path so this
        # stays a plain Win32 module that knows nothing about the handshake files.
        self.on_suspended = None
        self.on_resuming = None

    @property
    def expected_count(self) -> int:
        """How many hotkeys were queued for registration (#109 startup summary)."""
        return len(self._registrations)

    @property
    def registered_count(self) -> int:
        """How many hotkeys actually registered -- start() populates the map, so
        a per-key 1409 loss (another app owns the combo) shows as a shortfall
        against expected_count without changing start()'s return value (#61)."""
        return len(self._hotkey_map)

    def register(self, hotkey_str: str, callback, name: str = "") -> int:
        """
        Register a hotkey. Must be called before start().

        Args:
            hotkey_str: Hotkey string (e.g. 'ctrl+alt+w')
            callback: Function to call when hotkey is pressed
            name: Optional name for logging

        Returns:
            Hotkey ID (for reference)
        """
        hotkey_id = self._next_id
        self._next_id += 1
        self._registrations.append((hotkey_id, hotkey_str, callback, name))
        return hotkey_id

    def start(self) -> bool:
        """
        Start the listener thread. Blocks until all hotkeys are registered.

        Returns:
            True if started successfully
        """
        if self._thread is not None:
            logger.warning("HotkeyManager already started")
            return False

        self._started.clear()
        self._thread = threading.Thread(
            target=self._listener_thread,
            daemon=True,
            name="HotkeyListener"
        )
        self._thread.start()

        # Wait for registration to complete
        self._started.wait(timeout=5.0)
        if not self._started.is_set():
            logger.error("HotkeyManager: registration timeout")
            return False

        return True

    def suspend(self) -> bool:
        """Ask the listener to release every registration (#335). Post-only.

        True means the message was QUEUED, not that the hotkeys are already free --
        the listener works its queue in order, so a suspend posted while a callback
        (a transcription) is running runs after it returns. Whoever needs to know
        that it really happened waits for `on_suspended`, which fires there.

        `_started` is the sync point: the queue a post needs exists only once the
        listener has called into user32, which start() waits for. Before that (and
        after stop()) this returns False and the caller simply tries again.
        """
        return self._post_to_listener(WM_APP_SUSPEND_HOTKEYS)

    def resume(self) -> bool:
        """The mirror of suspend(): ask the listener to register everything again.
        Post-only, same gating; `on_resuming` fires there as the rebuild begins."""
        return self._post_to_listener(WM_APP_RESUME_HOTKEYS)

    def _post_to_listener(self, message) -> bool:
        if self._thread is None or self._thread_id is None or not self._started.is_set():
            return False
        return bool(PostThreadMessageW(self._thread_id, message, 0, 0))

    def stop(self):
        """Stop the listener thread and unregister all hotkeys."""
        if self._thread is None or self._thread_id is None:
            return

        logger.info("Stopping HotkeyManager...", extra={'file_only': True})
        # Send WM_QUIT to the message pump thread
        PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

        # If stop() runs on the listener thread itself (an exit hotkey's callback
        # executes here), we cannot join ourselves -- RuntimeError. The WM_QUIT
        # above unwinds the pump when the callback returns; run()'s finally then
        # calls stop() again from the main thread to do the real join.
        if threading.current_thread() is self._thread:
            logger.debug("stop() on listener thread; deferring join to main thread")
            return

        # Wait for thread to finish
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            logger.warning("HotkeyManager listener thread did not stop in time")
        else:
            logger.info("HotkeyManager stopped")

        self._thread = None
        self._thread_id = None

    def _register_all(self) -> list:
        """Register every queued hotkey and return the failures as (hotkey_str, name).

        Thread-affine by contract: RegisterHotKey binds a combo to the CALLING
        thread, so this only ever runs on the listener -- once at startup, and again
        on every #335 resume. get_last_error() is read in the failure branch only;
        after a success it still holds whatever the last failing call left (#165).
        """
        failed = []
        for hotkey_id, hotkey_str, callback, name in self._registrations:
            try:
                modifiers, vk_code = _parse_hotkey(hotkey_str)
                success = RegisterHotKey(None, hotkey_id, modifiers, vk_code)
                if success:
                    self._hotkey_map[hotkey_id] = (callback, name)
                    logger.info(f"  Registered: {hotkey_str} -> {name} (id={hotkey_id}, mod=0x{modifiers:04X}, vk=0x{vk_code:02X})", extra={'file_only': True})
                else:
                    failed.append((hotkey_str, name))
                    error_code = ctypes.get_last_error()
                    if error_code == 1409:
                        logger.error(f"  FAILED: {hotkey_str} -> {name} - Already registered by another application (Error 1409)", extra={'file_only': True})
                    else:
                        logger.error(f"  FAILED: {hotkey_str} -> {name} - RegisterHotKey failed (Error {error_code})", extra={'file_only': True})
            except ValueError as e:
                failed.append((hotkey_str, name))
                logger.error(f"  FAILED: {hotkey_str} -> {name} - Parse error: {e}", extra={'file_only': True})
        return failed

    def _unregister_all(self) -> int:
        """Release every live registration and empty the map; returns how many.

        The count is returned rather than logged because its two callers report very
        different events: the pump's exit is a shutdown, a #335 suspend is the tool
        stepping aside for a moment, and a log reader must be able to tell them apart.
        """
        released = len(self._hotkey_map)
        for hotkey_id in self._hotkey_map:
            UnregisterHotKey(None, hotkey_id)
        self._hotkey_map.clear()
        return released

    def _notify(self, callback, label):
        """Run one of the #335 completion hooks, containing whatever it raises: this
        is the message pump, so an escaping exception would take every hotkey with it.
        """
        if callback is None:
            return
        try:
            callback()
        except Exception as e:
            logger.error(f"Error in {label} callback: {e}", exc_info=True)

    def _listener_thread(self):
        """
        Listener thread: registers hotkeys, runs message pump, unregisters on exit.

        Callbacks run directly on this thread (serialized, no race conditions).
        This matches the behavior of the keyboard library's listener thread.
        """
        # Get Win32 thread ID (needed for PostThreadMessageW)
        self._thread_id = GetCurrentThreadId()
        logger.info(f"HotkeyManager listener thread started (Win32 TID: {self._thread_id})", extra={'file_only': True})

        # Register all hotkeys. The summary line and _started stay OUT of
        # _register_all: a #335 resume registers the same set again and must neither
        # claim a second "registration complete" nor re-arm the startup gate.
        self._register_all()
        logger.info(f"Hotkey registration complete: {len(self._hotkey_map)}/{len(self._registrations)} successful", extra={'file_only': True})

        # Signal that registration is done
        self._started.set()

        # Message pump loop
        msg = ctypes.wintypes.MSG()
        while True:
            ret = GetMessageW(ctypes.byref(msg), None, 0, 0)

            if ret == 0:
                # WM_QUIT received
                logger.debug("WM_QUIT received, exiting message pump")
                break
            elif ret == -1:
                logger.error("GetMessageW returned -1 (error)")
                break

            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                entry = self._hotkey_map.get(hotkey_id)
                if entry:
                    callback, name = entry
                    logger.debug(f"Hotkey triggered: {name} (id={hotkey_id})")
                    try:
                        callback()
                    except Exception as e:
                        logger.error(f"Error in hotkey callback '{name}': {e}", exc_info=True)

            elif msg.message == WM_APP_SUSPEND_HOTKEYS:
                # #335: step aside so the settings app's capture field can see a combo
                # this tool holds. Idempotent -- a second suspend finds nothing to
                # release and just confirms again. A WM_HOTKEY already queued behind
                # this one meets the empty map and is dropped by the guard above,
                # which is right: that press happened while we still held the combo.
                released = self._unregister_all()
                if released:
                    logger.info(f"Hotkeys released for a settings capture ({released} total)",
                                extra={'file_only': True})
                self._notify(self.on_suspended, "on_suspended")

            elif msg.message == WM_APP_RESUME_HOTKEYS:
                # The hook runs BEFORE the first RegisterHotKey, not after the last:
                # it is what takes the app's ACK off disk, and an ACK still lying
                # there while a combo is live again would let the capture field arm on
                # hotkeys the tool has already taken back. The other order is only
                # milliseconds wrong on a good day and permanently wrong on a bad one
                # (a removal that fails). This way the ACK can cost an arm, never
                # mis-arm one, which is the direction the whole handshake fails in.
                self._notify(self.on_resuming, "on_resuming")
                if not self._hotkey_map:      # idempotent too: nothing to rebuild
                    failed = self._register_all()
                    if failed:
                        # The one visible line this path owes (#335): a combo another
                        # application grabbed while we stood aside -- minutes, if the
                        # field stayed armed that long (D-031) -- is lost until the next
                        # start, and losing one silently is the single outcome this path
                        # must never produce.
                        combos = ", ".join(f"{s} ({n})" for s, n in failed)
                        logger.warning("Hotkey(s) could not be registered again after the "
                                       f"settings capture -- another application now holds: {combos}")
                    else:
                        logger.info("Hotkeys registered again after the settings capture "
                                    f"({len(self._hotkey_map)} total)", extra={'file_only': True})

        # Unregister all hotkeys
        released = self._unregister_all()
        logger.info(f"All hotkeys unregistered ({released} total)", extra={'file_only': True})
