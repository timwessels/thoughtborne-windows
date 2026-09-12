"""
Press-edge detector for mouse hotkeys (Issue #308).

Pure, Win32-decoupled: feed it one down/up reading per watched virtual key per
tick and it returns the hotkey ids whose PRESS edge fell in this tick. The Win32
reads (GetAsyncKeyState via hotkey_manager.is_vk_pressed), the 10 ms clock and the
delivery (PostThreadMessageW to the listener thread) live in thoughtborne.py,
exactly as ptt_detector's do -- so the rules can be driven with synthetic
sequences on plain Python, which is the only thing about this lane that is
checkable off Windows at all.

Why the lane exists: RegisterHotKey accepts a mouse virtual key, returns TRUE and
then never fires for it (measured 2026-09-08), so a mouse button cannot ride the
reservation mechanism the keyboard hotkeys use. It is polled instead, and it is
never exclusive -- other programs keep reacting to the same button.

Edges are built from successive high-bit reads plus this object's own memory, the
rule the 2026-09-08 Win32 study settles on: GetAsyncKeyState's "pressed since the
last call" low bit is consumed by whoever queries first and is documented to
report false negatives, so it is not what an edge is made of. At a 10 ms clock it
is not needed either -- a mouse switch's own bounce outlasts a tick.

The keyboard plays no part. A mouse hotkey fires on its press edge whatever keys
are held at that moment: there is no chord to protect (unlike push-to-talk's
bare-trigger rule), and a held modifier silently swallowing a dictation start
would be the worst failure this lane could have.
"""


class MouseEdgeDetector:
    """Rising-edge detector over a fixed set of (vk, hotkey_id) bindings.

    State is kept per virtual key rather than per binding, so two bindings on the
    same button would fire only the first. Configuration cannot produce that --
    config.apply_hotkey_overrides drops a combo that collides with another
    action's -- and this object leans on that instead of keying by index.
    """

    def __init__(self, bindings):
        """bindings: [(vk, hotkey_id)] -- what to watch, from HotkeyManager."""
        self._bindings = list(bindings)
        self._down = {vk: False for vk, _ in self._bindings}
        self._primed = False

    @property
    def watched_vks(self):
        """The virtual keys the caller has to read this tick, in binding order."""
        return [vk for vk, _ in self._bindings]

    def tick(self, down) -> list:
        """down: {vk: bool} -> the ids to fire, in binding order.

        The FIRST tick primes and fires nothing: a button already held when the
        detector is built must not read as a press the user just made -- the same
        discipline ptt_detector keeps with _prev_trigger, for the same reason.
        Every later tick fires on a rising edge only, so a held button fires once,
        however long it stays down.

        A vk missing from `down` counts as up: a reading that could not be taken
        must never invent a press.
        """
        fired = []
        for vk, hotkey_id in self._bindings:
            is_down = bool(down.get(vk, False))
            if self._primed and is_down and not self._down[vk]:
                fired.append(hotkey_id)
            self._down[vk] = is_down
        self._primed = True
        return fired
