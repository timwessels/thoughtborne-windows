"""
Push-to-talk gesture detector (Issue #66).

Pure, Win32-decoupled state machine: feed it a KeyboardSnapshot plus a
monotonic timestamp once per tick, and it returns a PttAction. It recognizes
double-tap-and-hold on a bare trigger modifier (default Left-Ctrl), filtered
against AltGr (a Right-Alt co-press) and against any foreign key (which
disarms the gesture).

The Win32 reads and the wiring into the recording loop live in
thoughtborne.py; this module imports nothing platform-specific (no ctypes,
no user32), so the gesture rules can be unit-tested with synthetic sequences
on any OS, including the Linux/WSL build environment.
"""
import enum
from dataclasses import dataclass


class PttAction(enum.Enum):
    NONE = 0
    START = 1   # hold confirmed -> caller starts a recording
    STOP = 2    # trigger released during recording -> caller stops + inserts


@dataclass(frozen=True)
class KeyboardSnapshot:
    """One tick's worth of keyboard state, already reduced to three booleans.

    trigger_down: the configured trigger modifier is physically down.
    blocker_down: the AltGr discriminator (Right-Alt) is physically down.
    foreign_down: any key other than the trigger / the Ctrl pair / Right-Alt
                  is physically down (only consulted while arming; see below).
    """
    trigger_down: bool
    blocker_down: bool
    foreign_down: bool


class _S(enum.Enum):
    IDLE = 0          # waiting for the first tap-down
    TAP_HELD = 1      # first press is down, waiting for its release
    ARMED = 2         # first tap released, within the tap window, waiting for press #2
    HOLD_PENDING = 3  # second press is down, waiting to reach min-hold
    RECORDING = 4     # min-hold reached, the recording is owned by PTT


# States where a foreign key (or AltGr) is irrelevant to the detector, so the
# caller can skip the comparatively expensive foreign-key scan: IDLE only needs
# the trigger edge to begin, and RECORDING is ended solely by trigger release
# (foreign keys are deliberately allowed mid-recording so the user can dictate
# and type at the same time).
_STEADY_STATES = (_S.IDLE, _S.RECORDING)


class PttDetector:
    def __init__(self, tap_window_s, min_hold_s, release_tail_s):
        self.tap_window_s = tap_window_s
        self.min_hold_s = min_hold_s
        self.release_tail_s = release_tail_s
        self._state = _S.IDLE
        self._t_edge = 0.0           # timestamp of the last meaningful edge
        self._prev_trigger = False   # previous trigger_down, for edge detection
        self._release_at = None      # release-tail bookkeeping during RECORDING

    def reset(self):
        """Force the machine back to IDLE.

        Called when PTT must go inert (a non-PTT recording started) or after a
        failed start. _prev_trigger is intentionally NOT reset: if the trigger
        is still physically down, keeping the previous value prevents the very
        next tick from reading a phantom rising edge and re-arming on a key the
        user never actually re-pressed.
        """
        self._state = _S.IDLE
        self._release_at = None

    def needs_foreign_scan(self) -> bool:
        """Whether the caller should bother polling foreign keys this tick.

        False in the steady states (IDLE / RECORDING) where foreign_down is
        never consulted, so the caller can pass foreign_down=False and skip the
        scan entirely. True only during the brief arming window.
        """
        return self._state not in _STEADY_STATES

    def update(self, snap: KeyboardSnapshot, now: float) -> PttAction:
        # AltGr / foreign-key veto: a synthetic Left-Ctrl injected by AltGr always
        # co-presses Right-Alt (blocker_down), and any real chord (e.g. Ctrl+C)
        # presses a foreign key. Either condition means this trigger press is not
        # a bare PTT gesture.
        bare = snap.trigger_down and not snap.blocker_down and not snap.foreign_down

        # Edge detection is built from successive high-bit reads, as the spike
        # mandates: GetAsyncKeyState's "pressed since last call" low bit is
        # documented-unreliable, so the detector tracks rising/falling edges itself.
        trig_edge_down = snap.trigger_down and not self._prev_trigger
        trig_edge_up = (not snap.trigger_down) and self._prev_trigger
        self._prev_trigger = snap.trigger_down

        # Disarm anywhere in the arming phase if a foreign key or the AltGr blocker
        # is present. This is what keeps Ctrl+C -> Ctrl+V (and every other Ctrl
        # combo) from ever reaching START. During RECORDING a foreign key does NOT
        # stop the recording -- only trigger release does.
        if self._state in (_S.TAP_HELD, _S.ARMED, _S.HOLD_PENDING):
            if snap.foreign_down or snap.blocker_down:
                self._state = _S.IDLE
                return PttAction.NONE

        if self._state is _S.IDLE:
            if trig_edge_down and bare:
                self._state = _S.TAP_HELD
                self._t_edge = now
            return PttAction.NONE

        if self._state is _S.TAP_HELD:
            if trig_edge_up:
                self._state = _S.ARMED
                self._t_edge = now
            return PttAction.NONE

        if self._state is _S.ARMED:
            if now - self._t_edge > self.tap_window_s:
                self._state = _S.IDLE          # too slow -> not a double-tap
            elif trig_edge_down and bare:
                self._state = _S.HOLD_PENDING
                self._t_edge = now
            return PttAction.NONE

        if self._state is _S.HOLD_PENDING:
            if trig_edge_up:
                self._state = _S.IDLE          # released before min-hold -> abort
            elif now - self._t_edge >= self.min_hold_s:
                self._state = _S.RECORDING
                return PttAction.START
            return PttAction.NONE

        if self._state is _S.RECORDING:
            # Release tail: once the trigger goes up, wait release_tail_s of
            # continued "up" before emitting STOP, so word-ends are not clipped.
            # Known trade-off: a trigger press inside the tail window cancels the
            # pending stop, so an unrelated re-press (e.g. Ctrl for a Ctrl+C right
            # after release) keeps the recording alive until the next release --
            # a slightly longer recording that self-corrects on that release; the
            # user's Ctrl+C still works because polling never suppresses keys.
            if snap.trigger_down:
                self._release_at = None        # re-pressed within the tail: cancel stop
                return PttAction.NONE
            if self._release_at is None:
                self._release_at = now
                return PttAction.NONE
            if now - self._release_at >= self.release_tail_s:
                self._state = _S.IDLE
                self._release_at = None
                return PttAction.STOP
            return PttAction.NONE

        return PttAction.NONE
