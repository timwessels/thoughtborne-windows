"""
Hotkey Manager Module - Win32 RegisterHotKey API

Replaces keyboard.add_hotkey() with the Win32 RegisterHotKey API.
RegisterHotKey survives Connected Standby (sleep/wake), unlike the
WH_KEYBOARD_LL hook used by the keyboard library.

Also provides is_key_pressed() using GetAsyncKeyState as a hook-free
replacement for keyboard.is_pressed().

One kind of hotkey does not go through RegisterHotKey at all: a mouse button
(#308), which the API accepts and then never fires for. Those are filed into a
polled lane instead -- register() sorts them there, a poll thread in
thoughtborne.py reads their key state through is_vk_pressed() and posts the hit
back here via post_hotkey(), so the pump dispatches it and the callback runs on
this thread like every other one.

Public API:
    HotkeyManager:
        register(hotkey_str, callback, name="") -> int
        start() -> bool
        stop()
        mouse_bindings() -> [(vk, hotkey_id)]      # the polled lane (#308)
        post_hotkey(hotkey_id) -> bool             # deliver a polled hit

    is_key_pressed(key_name: str) -> bool
"""

import ctypes
import ctypes.wintypes
import logging
import threading

# The modifier flags, the modifier map, the static VK map (letters/digits/F-keys)
# and the structural parser live in the pure, ctypes-free hotkey_parse module (#55)
# so config can share the exact same lexical layer without importing this
# Windows-bound module. Re-exported here (imported names) so any consumer of
# hotkey_manager.MOD_* / MODIFIER_MAP / VK_MAP keeps working unchanged.
from hotkey_parse import (
    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT,
    MODIFIER_MAP, VK_MAP, HotkeyParseError, parse_hotkey_lexical, mouse_vk,
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
# Spelled out like every other function in this block: the thread id is a DWORD
# and wParam is pointer-wide, so a hotkey id posted from the polled lane (#308)
# cannot be silently truncated, and post_hotkey's return value is a real BOOL.
PostThreadMessageW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.UINT,
                               ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
PostThreadMessageW.restype = ctypes.wintypes.BOOL

GetAsyncKeyState = user32.GetAsyncKeyState
GetAsyncKeyState.argtypes = [ctypes.c_int]
GetAsyncKeyState.restype = ctypes.c_short

VkKeyScanW = user32.VkKeyScanW
VkKeyScanW.argtypes = [ctypes.wintypes.WCHAR]
VkKeyScanW.restype = ctypes.c_short

GetCurrentThreadId = kernel32.GetCurrentThreadId

# ===== VK Code Maps =====
# MODIFIER_MAP and VK_MAP (letters, digits, and F-keys) come from hotkey_parse (#55).

# Modifier string -> VK code (for GetAsyncKeyState)
VK_KEY_MAP = {
    'ctrl': 0x11,      # VK_CONTROL
    'control': 0x11,
    'alt': 0x12,        # VK_MENU
    'shift': 0x10,      # VK_SHIFT
    'win': 0x5B,        # VK_LWIN
    'windows': 0x5B,
}
# Merge letter/digit/F-key codes into VK_KEY_MAP for is_key_pressed. F-keys ride
# along automatically now that VK_MAP carries them, so is_key_pressed('f9') works.
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
    Resolve a key string to a VK code.

    For standard keys (a-z, 0-9, f1-f24) uses the static VK_MAP.
    For special characters (e.g. 'ue') uses VkKeyScanW for layout-aware resolution,
    with a fallback to VK_OEM_4 (0xDB) for German QWERTZ.

    Args:
        key_str: Key name (e.g. 'w', '4', 'ue')

    Returns:
        VK code as integer

    Raises:
        ValueError: If key cannot be resolved
    """
    # Check static map first
    if key_str in VK_MAP:
        return VK_MAP[key_str]

    # Try VkKeyScanW for special characters (e.g. umlauts)
    if len(key_str) == 1:
        result = VkKeyScanW(key_str)
        vk = result & 0xFF
        if vk != 0xFF:
            return vk

    # Specific fallbacks for known special keys
    if key_str in ('ü', 'ue'):
        # Try runtime resolution first
        result = VkKeyScanW('ü')
        vk = result & 0xFF
        if vk != 0xFF:
            return vk
        # Fallback: VK_OEM_4 (0xDB) - typically 'ü' on German QWERTZ
        logger.warning("VkKeyScanW failed for 'ü', using fallback VK_OEM_4 (0xDB)")
        return 0xDB

    raise ValueError(f"Cannot resolve key '{key_str}' to VK code")


def _parse_hotkey(hotkey_str: str) -> tuple:
    """
    Parse a hotkey string like 'ctrl+alt+w' into (modifiers, vk_code).

    Thin wrapper: the pure structural split (modifier/key validation, always
    setting MOD_NOREPEAT to prevent repeat when a key is held) lives in
    hotkey_parse.parse_hotkey_lexical (#55); the one Windows-bound step
    (_resolve_vk_code, VkKeyScanW for special characters) stays here.

    Args:
        hotkey_str: Hotkey string (e.g. 'ctrl+alt+w', 'ctrl+alt+4', or a user
            override's special key 'ctrl+alt+ü')

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
        # Try resolving as special character
        try:
            vk = _resolve_vk_code(key_lower)
        except ValueError:
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
        self._registrations = []        # keyboard: (hotkey_id, hotkey_str, callback, name)
        self._mouse_registrations = []  # polled:   (hotkey_id, hotkey_str, callback, name, vk)
        # hotkey_id -> (callback, name), for BOTH kinds: the pump must not be able
        # to tell a polled hit from a reserved one, which is what keeps every
        # callback running where and how it runs today.
        self._hotkey_map = {}
        self._registered_ids = []  # ids RegisterHotKey actually took (#308)
        self._thread = None
        self._thread_id = None    # Win32 thread ID for PostThreadMessageW
        self._started = threading.Event()
        self._next_id = 1

    @property
    def expected_count(self) -> int:
        """How many hotkeys were queued for RegisterHotKey (#109 startup summary).

        Mouse combos are deliberately not in it (#308): they hold no reservation,
        so counting them would make the summary answer a question nobody asked --
        "did the exclusive reservations succeed"."""
        return len(self._registrations)

    @property
    def registered_count(self) -> int:
        """How many hotkeys actually registered -- start() fills the id list, so
        a per-key 1409 loss (another app owns the combo) shows as a shortfall
        against expected_count without changing start()'s return value (#61)."""
        return len(self._registered_ids)

    @property
    def listening_count(self) -> int:
        """Mouse combos filed into the polled lane (#308). Reported beside the
        registered count, never added to it: listening is not a reservation."""
        return len(self._mouse_registrations)

    def register(self, hotkey_str: str, callback, name: str = "") -> int:
        """
        Register a hotkey. Must be called before start().

        A bare mouse combo goes into the polled lane instead of the
        RegisterHotKey one (#308); everything else is unchanged. The lane is
        decided by hotkey_parse.mouse_vk, which never raises, so this method
        stays unable to raise -- _register_hotkeys calls it in an unguarded loop
        before the listener thread exists.

        Args:
            hotkey_str: Hotkey string (e.g. 'ctrl+alt+w', or 'xbutton1')
            callback: Function to call when hotkey is pressed
            name: Optional name for logging

        Returns:
            Hotkey ID (for reference)
        """
        hotkey_id = self._next_id
        self._next_id += 1
        vk = mouse_vk(hotkey_str)
        if vk is not None:
            self._mouse_registrations.append(
                (hotkey_id, hotkey_str, callback, name, vk))
        else:
            self._registrations.append((hotkey_id, hotkey_str, callback, name))
        return hotkey_id

    def mouse_bindings(self) -> list:
        """[(vk, hotkey_id)] for the polled lane -- what the poll thread watches."""
        return [(vk, hotkey_id)
                for hotkey_id, _str, _cb, _name, vk in self._mouse_registrations]

    def post_hotkey(self, hotkey_id: int) -> bool:
        """Deliver a polled (mouse) hit to the listener thread as a WM_HOTKEY (#308).

        The pump dispatches by id and GetMessageW(hwnd=None) is what retrieves a
        thread message, so the callback runs on the listener thread exactly like a
        RegisterHotKey hit -- same thread, same serialization, same error
        handling. False when there is no listener thread (before start(), after
        stop()): the caller is a poll thread that can outlive either end, and a
        hit with nowhere to go is a no-op, never an error. Outliving stop() is
        the normal case rather than the edge: on the Ctrl+C/Ctrl+Break path the
        app's `running` flag is never cleared -- thoughtborne.py sets it in
        stop_program alone, and KeyboardInterrupt goes straight to run()'s
        finally -- so the poller keeps ticking through the whole of cleanup().
        It still needs no lock, because every way this ends is harmless: once
        the stop() that joins has run, both _thread and _thread_id are None and
        the check below catches it; a stop() landing between that check and the
        post sends to a thread id that no longer exists, which returns 0 and is
        gone; and a message that does arrive sits behind the WM_QUIT stop()
        already posted, in a queue nobody drains. A lock in a 100 Hz path to
        serialize that would be the worse trade.
        """
        thread_id = self._thread_id
        if thread_id is None or self._thread is None:
            return False
        return bool(PostThreadMessageW(thread_id, WM_HOTKEY, hotkey_id, 0))

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

    def _listener_thread(self):
        """
        Listener thread: registers hotkeys, runs message pump, unregisters on exit.

        Callbacks run directly on this thread (serialized, no race conditions).
        This matches the behavior of the keyboard library's listener thread.
        """
        # Get Win32 thread ID (needed for PostThreadMessageW)
        self._thread_id = GetCurrentThreadId()
        logger.info(f"HotkeyManager listener thread started (Win32 TID: {self._thread_id})", extra={'file_only': True})

        # Register all hotkeys
        registered_count = 0
        for hotkey_id, hotkey_str, callback, name in self._registrations:
            try:
                modifiers, vk_code = _parse_hotkey(hotkey_str)
                success = RegisterHotKey(None, hotkey_id, modifiers, vk_code)
                if success:
                    self._hotkey_map[hotkey_id] = (callback, name)
                    self._registered_ids.append(hotkey_id)
                    logger.info(f"  Registered: {hotkey_str} -> {name} (id={hotkey_id}, mod=0x{modifiers:04X}, vk=0x{vk_code:02X})", extra={'file_only': True})
                    registered_count += 1
                else:
                    error_code = ctypes.get_last_error()
                    if error_code == 1409:
                        logger.error(f"  FAILED: {hotkey_str} -> {name} - Already registered by another application (Error 1409)", extra={'file_only': True})
                    else:
                        logger.error(f"  FAILED: {hotkey_str} -> {name} - RegisterHotKey failed (Error {error_code})", extra={'file_only': True})
            except ValueError as e:
                logger.error(f"  FAILED: {hotkey_str} -> {name} - Parse error: {e}", extra={'file_only': True})

        logger.info(f"Hotkey registration complete: {registered_count}/{len(self._registrations)} successful", extra={'file_only': True})

        # Mouse combos are LISTENING, not registered (#308): no reservation
        # exists for them, a poll thread on its own 10 ms clock reads their key
        # state and posts the hit here. They go into the dispatch map but never
        # into _registered_ids -- there is nothing to unregister and nothing that
        # counts as a successful reservation. Filed before _started.set(), so a
        # click the instant start() returns already dispatches. The line is the
        # one trace a user who binds by hand gets on every start, and it says out
        # loud that the button stays shared with every other program.
        for hotkey_id, hotkey_str, callback, name, vk in self._mouse_registrations:
            self._hotkey_map[hotkey_id] = (callback, name)
            logger.info(f"  Listening (mouse, not exclusive): {hotkey_str} -> {name} "
                        f"(id={hotkey_id}, vk=0x{vk:02X})", extra={'file_only': True})

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

        # Unregister all hotkeys. Only the ids RegisterHotKey actually took (#308)
        # -- a polled mouse id was never reserved, so unregistering it would be a
        # meaningless call and would make the count below lie.
        for hotkey_id in self._registered_ids:
            UnregisterHotKey(None, hotkey_id)
        logger.info(f"All hotkeys unregistered ({len(self._registered_ids)} total)", extra={'file_only': True})
        self._registered_ids.clear()
        self._hotkey_map.clear()
