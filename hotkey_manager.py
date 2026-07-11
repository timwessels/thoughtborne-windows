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
        stop()

    is_key_pressed(key_name: str) -> bool
"""

import ctypes
import ctypes.wintypes
import logging
import threading

logger = logging.getLogger('Thoughtborne.HotkeyManager')

# ===== Win32 Constants =====
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

# ===== Win32 Functions =====
user32 = ctypes.windll.user32
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

VkKeyScanW = user32.VkKeyScanW
VkKeyScanW.argtypes = [ctypes.wintypes.WCHAR]
VkKeyScanW.restype = ctypes.c_short

GetCurrentThreadId = kernel32.GetCurrentThreadId

# ===== VK Code Maps =====

# Modifier string -> RegisterHotKey modifier flag
MODIFIER_MAP = {
    'ctrl': MOD_CONTROL,
    'control': MOD_CONTROL,
    'alt': MOD_ALT,
    'shift': MOD_SHIFT,
    'win': MOD_WIN,
    'windows': MOD_WIN,
}

# Key string -> VK code (for non-modifier keys)
VK_MAP = {}
# Letters A-Z
for i in range(26):
    VK_MAP[chr(ord('a') + i)] = 0x41 + i
# Digits 0-9
for i in range(10):
    VK_MAP[str(i)] = 0x30 + i

# Modifier string -> VK code (for GetAsyncKeyState)
VK_KEY_MAP = {
    'ctrl': 0x11,      # VK_CONTROL
    'control': 0x11,
    'alt': 0x12,        # VK_MENU
    'shift': 0x10,      # VK_SHIFT
    'win': 0x5B,        # VK_LWIN
    'windows': 0x5B,
}
# Merge letter/digit codes into VK_KEY_MAP for is_key_pressed
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

    For standard keys (a-z, 0-9) uses the static VK_MAP.
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
        logger.warning(f"VkKeyScanW failed for 'ü', using fallback VK_OEM_4 (0xDB)")
        return 0xDB

    raise ValueError(f"Cannot resolve key '{key_str}' to VK code")


def _parse_hotkey(hotkey_str: str) -> tuple:
    """
    Parse a hotkey string like 'ctrl+alt+w' into (modifiers, vk_code).

    MOD_NOREPEAT is always added to prevent repeat when key is held.

    Args:
        hotkey_str: Hotkey string (e.g. 'ctrl+alt+w', 'ctrl+alt+4', 'ctrl+alt+ü')

    Returns:
        Tuple of (modifier_flags, vk_code)

    Raises:
        ValueError: If parsing fails
    """
    parts = hotkey_str.lower().split('+')
    modifiers = MOD_NOREPEAT  # Always set
    key_part = None

    for part in parts:
        part = part.strip()
        if part in MODIFIER_MAP:
            modifiers |= MODIFIER_MAP[part]
        else:
            if key_part is not None:
                raise ValueError(f"Multiple non-modifier keys in '{hotkey_str}': '{key_part}' and '{part}'")
            key_part = part

    if key_part is None:
        raise ValueError(f"No non-modifier key found in '{hotkey_str}'")

    vk_code = _resolve_vk_code(key_part)
    return (modifiers, vk_code)


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
        self._registrations = []  # List of (hotkey_str, callback, name)
        self._hotkey_map = {}     # hotkey_id -> (callback, name)
        self._thread = None
        self._thread_id = None    # Win32 thread ID for PostThreadMessageW
        self._started = threading.Event()
        self._next_id = 1

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
                    logger.info(f"  Registered: {hotkey_str} -> {name} (id={hotkey_id}, mod=0x{modifiers:04X}, vk=0x{vk_code:02X})", extra={'file_only': True})
                    registered_count += 1
                else:
                    error_code = ctypes.get_last_error()
                    if error_code == 1409:
                        logger.error(f"  FAILED: {hotkey_str} -> {name} - Already registered by another application (Error 1409)")
                    else:
                        logger.error(f"  FAILED: {hotkey_str} -> {name} - RegisterHotKey failed (Error {error_code})")
            except ValueError as e:
                logger.error(f"  FAILED: {hotkey_str} -> {name} - Parse error: {e}")

        logger.info(f"Hotkey registration complete: {registered_count}/{len(self._registrations)} successful", extra={'file_only': True})

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

        # Unregister all hotkeys
        for hotkey_id in self._hotkey_map:
            UnregisterHotKey(None, hotkey_id)
        logger.info(f"All hotkeys unregistered ({len(self._hotkey_map)} total)", extra={'file_only': True})
        self._hotkey_map.clear()
