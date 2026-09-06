#!/usr/bin/env python3
"""Off-Windows verification of the push-to-talk gesture rules (#66, #242).

`ptt_detector` is the project's one deliberately Win32-decoupled module: it imports
nothing but `enum` and `dataclasses`, takes the keyboard state as three booleans and
the clock as a parameter. The whole state machine can therefore be driven with
synthetic, perfectly deterministic sequences on plain Python -- no stubs, no tempdir,
no display -- which is what its own docstring promises. Until #242 nothing tested it.

Every case compares the EXACT action list, one entry per tick, not "a START happened
somewhere": the timing bugs this driver exists to catch (a min-hold comparison that
fires one tick early, a release tail measured from the wrong release) still produce a
START and a STOP -- just at the wrong tick.

What is pinned:

  - the happy gesture with all three window comparisons landing exactly ON their
    boundary: a second press whose gap equals the tap window is still accepted
    (strict `>` expiry), a hold whose delta equals min-hold starts (`>=`), a release
    tail whose delta equals the tail stops (`>=`);
  - the two independent vetoes. The disarm block drops any arming state the moment a
    foreign key or AltGr's Right-Alt appears; the `bare` term additionally refuses to
    ARM on a press that is not alone. The second is defence in depth: with it removed,
    every ordinary sequence below stays green (#242 reports the same for a
    20,000-step fuzz) and only a hand-built one diverges -- a foreign key released
    while the trigger stays down. That is `check_veto_special_case_*` below; without
    it the ladder cannot see the veto disappear;
  - `needs_foreign_scan()` and the `_STEADY_STATES` set behind it. The recording loop
    in `thoughtborne.py` (`_ptt_tick`) passes `foreign_down=False` whenever this
    returns False, so an arming state that ever joined the steady set would silently
    kill the foreign-key veto for that state -- the detector would keep reading a
    hardcoded False and never know;
  - `reset()`: it clears the state but deliberately keeps `_prev_trigger`, so a
    trigger that is still physically down cannot be misread as a fresh press.

    python3 test_ptt_detector.py    # verify, exit non-zero on any violation
"""
import sys

import ptt_detector
from ptt_detector import KeyboardSnapshot, PttAction, PttDetector

# The three windows are constructor parameters (config's shipped defaults are
# 0.30/0.20/0.15 and users may override them), so the driver brings its own -- as
# multiples of 1/16 s, which floats represent exactly. That is not cosmetic: with the
# shipped values a "boundary" tick built as 0.55 - 0.35 comes out 0.20000000000000007,
# i.e. just OVER min-hold, and would pin nothing about the `>=`. Every timestamp below
# is a multiple of QUANTUM so `now - t_edge` is exact and the boundary really is one.
QUANTUM = 0.0625
TAP_WINDOW_S = 0.3125    # 5/16 s, near the shipped 0.30
MIN_HOLD_S = 0.1875      # 3/16 s, near the shipped 0.20
RELEASE_TAIL_S = 0.125   # 2/16 s, near the shipped 0.15

DOWN = True
UP = False

NONE = PttAction.NONE
START = PttAction.START
STOP = PttAction.STOP

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def new_detector():
    return PttDetector(TAP_WINDOW_S, MIN_HOLD_S, RELEASE_TAIL_S)


def play(det, steps):
    """Feed one tick per step and return the action list.

    A step is `(now, trigger)` or `(now, trigger, blocker)` or
    `(now, trigger, blocker, foreign)`; the omitted booleans are False.
    """
    actions = []
    for step in steps:
        now, trigger = step[0], step[1]
        blocker = step[2] if len(step) > 2 else False
        foreign = step[3] if len(step) > 3 else False
        actions.append(det.update(KeyboardSnapshot(trigger, blocker, foreign), now))
    return actions


def show(actions):
    """Readable action list for failure messages."""
    return "[" + ", ".join(a.name for a in actions) + "]"


def check_boundary_fixture_is_exact():
    """The boundary cases below are only boundaries if the arithmetic is exact.

    Guards a future "let's use the shipped 0.30/0.20/0.15 here" edit, which would move
    two of the three comparisons a float epsilon off the boundary and quietly turn the
    equality pins into inequality pins.
    """
    check(0.375 - 0.0625 == TAP_WINDOW_S,
          "tap-window boundary ticks are not exactly one window apart")
    check(0.5625 - 0.375 == MIN_HOLD_S,
          "min-hold boundary ticks are not exactly one min-hold apart")
    check(0.8125 - 0.6875 == RELEASE_TAIL_S,
          "release-tail boundary ticks are not exactly one tail apart")


def check_happy_gesture():
    """Tap, release, press-and-hold -> START; release + tail -> STOP.

    All three window comparisons sit exactly on their boundary: the second press comes
    one tap window after the first release (accepted, expiry is strict `>`), the hold
    reaches exactly min-hold (starts, `>=`), the tail reaches exactly release_tail
    (stops, `>=`).
    """
    det = new_detector()
    actions = play(det, [
        (0.0000, DOWN),   # first tap down
        (0.0625, UP),     # first tap released -> armed
        (0.3750, DOWN),   # gap == tap window: still accepted
        (0.5000, DOWN),   # held 2/16 s: below min-hold
        (0.5625, DOWN),   # held exactly min-hold -> START
        (0.6250, DOWN),
        (0.6875, UP),     # release: the tail begins
        (0.7500, UP),     # 1/16 s into the tail: not yet
        (0.8125, UP),     # exactly release_tail -> STOP
        (0.8750, UP),     # back in IDLE: nothing more
    ])
    expected = [NONE, NONE, NONE, NONE, START, NONE, NONE, NONE, STOP, NONE]
    check(actions == expected, f"happy gesture: got {show(actions)}")


def check_min_hold_boundary():
    """One quantum below min-hold is silent; exactly min-hold starts.

    The two sides of the `>=` in one sequence -- an inverted comparison flips both.
    """
    det = new_detector()
    actions = play(det, [
        (0.0000, DOWN),
        (0.0625, UP),
        (0.1250, DOWN),   # second press: the hold starts here
        (0.2500, DOWN),   # min-hold minus one quantum
        (0.3125, DOWN),   # exactly min-hold
    ])
    check(actions == [NONE, NONE, NONE, NONE, START],
          f"min-hold boundary: got {show(actions)}")


def check_tap_expiry():
    """A second press after the tap window is not a double-tap -- and stays dead.

    The expired press is swallowed: it re-enters IDLE with the trigger already down,
    so there is no rising edge left to arm on, no matter how long the user keeps
    holding.
    """
    det = new_detector()
    actions = play(det, [
        (0.0000, DOWN),
        (0.0625, UP),
        (0.4375, DOWN),   # gap 6/16 s > tap window -> expiry wins over the edge
        (0.5000, DOWN),
        (0.7500, DOWN),   # held far past min-hold: still nothing
        (0.8125, UP),
    ])
    check(actions == [NONE] * 6, f"tap expiry: got {show(actions)}")
    check(det.needs_foreign_scan() is False,
          "tap expiry left the detector out of IDLE (foreign scan still requested)")


def check_early_release():
    """Releasing the second press before min-hold aborts the gesture."""
    det = new_detector()
    actions = play(det, [
        (0.0000, DOWN),
        (0.0625, UP),
        (0.1250, DOWN),
        (0.1875, UP),     # released one quantum in -> abort, back to IDLE
    ])
    check(actions == [NONE] * 4, f"early release: got {show(actions)}")
    check(det.needs_foreign_scan() is False,
          "an aborted gesture still requests the foreign-key scan")

    # A fresh press afterwards is a first tap again, never a resumed hold.
    more = play(det, [(0.2500, DOWN), (0.7500, DOWN)])
    check(more == [NONE, NONE], f"early release, held again: got {show(more)}")


def check_altgr_veto_disarms():
    """Right-Alt (AltGr's discriminator) anywhere in the arming phase disarms.

    AltGr injects a synthetic Left-Ctrl, so every umlaut-keyboard `@ \\ { } [ ] | ~`
    would otherwise look like a trigger press. Checked in all three arming states.
    """
    det = new_detector()
    tap_held = play(det, [
        (0.0000, DOWN),
        (0.0625, DOWN, True),   # AltGr while the first tap is still down
        (0.1250, DOWN),
        (0.3750, DOWN),
        (0.4375, UP),
    ])
    check(tap_held == [NONE] * 5, f"AltGr during TAP_HELD: got {show(tap_held)}")

    det = new_detector()
    armed = play(det, [
        (0.0000, DOWN),
        (0.0625, UP),
        (0.1250, UP, True),     # AltGr while armed
        (0.1875, DOWN),
        (0.5000, DOWN),
    ])
    check(armed == [NONE] * 5, f"AltGr during ARMED: got {show(armed)}")

    det = new_detector()
    hold_pending = play(det, [
        (0.0000, DOWN),
        (0.0625, UP),
        (0.1250, DOWN),
        (0.1875, DOWN, True),   # AltGr while the hold is building
        (0.5000, DOWN),         # kept down far past min-hold: still no START
    ])
    check(hold_pending == [NONE] * 5,
          f"AltGr during HOLD_PENDING: got {show(hold_pending)}")


def check_foreign_veto_disarms():
    """Any foreign key disarms -- this is what keeps Ctrl+C -> Ctrl+V out of PTT."""
    det = new_detector()
    hold_pending = play(det, [
        (0.0000, DOWN),
        (0.0625, UP),
        (0.1250, DOWN),
        (0.1875, DOWN, False, True),   # a content key joins the held trigger
        (0.5000, DOWN),
    ])
    check(hold_pending == [NONE] * 5,
          f"foreign key during HOLD_PENDING: got {show(hold_pending)}")

    # The real chord, end to end: Ctrl+C, then Ctrl+V.
    det = new_detector()
    chord = play(det, [
        (0.0000, DOWN),                # Ctrl down
        (0.0625, DOWN, False, True),   # C down -> disarm
        (0.1250, DOWN),                # C up, Ctrl still down
        (0.1875, UP),                  # Ctrl up
        (0.2500, DOWN),                # Ctrl down again
        (0.3125, DOWN, False, True),   # V down -> disarm
        (0.3750, UP),
    ])
    check(chord == [NONE] * 7, f"Ctrl+C -> Ctrl+V: got {show(chord)}")


def check_veto_special_case_foreign():
    """A foreign key released while the trigger stays down must not arm.

    The disarm block cannot see this one: in IDLE it does not run, and by the next
    tick the foreign key is already gone. Only the `bare` term refuses the press, and
    only that refusal keeps the released-key tap out of the gesture. Removing the term
    turns this exact sequence into a START on the last tick.
    """
    det = new_detector()
    actions = play(det, [
        (0.0000, DOWN, False, True),   # trigger goes down together with another key
        (0.0625, DOWN),                # the other key is released, trigger stays down
        (0.1250, UP),                  # trigger up -- would be "tap #1" if it had armed
        (0.1875, DOWN),                # press #2
        (0.5000, DOWN),                # held past min-hold
    ])
    check(actions == [NONE] * 5,
          f"foreign-key release with the trigger held: got {show(actions)}")


def check_veto_special_case_blocker():
    """The AltGr side of the same hole: Right-Alt down on the arming press."""
    det = new_detector()
    actions = play(det, [
        (0.0000, DOWN, True),
        (0.0625, DOWN),
        (0.1250, UP),
        (0.1875, DOWN),
        (0.5000, DOWN),
    ])
    check(actions == [NONE] * 5,
          f"AltGr release with the trigger held: got {show(actions)}")


def check_release_tail_repress():
    """A press inside the tail cancels the pending stop; the next release restarts it.

    The tick at 0.625 is the discriminator: measured from the FIRST release it would
    already be a STOP, so a tail that forgot to restart shows up here.
    """
    det = new_detector()
    actions = play(det, [
        (0.0000, DOWN),
        (0.0625, UP),
        (0.1250, DOWN),
        (0.3125, DOWN),   # min-hold reached -> START
        (0.3750, UP),     # first release: tail begins
        (0.4375, DOWN),   # re-pressed inside the tail -> pending stop cancelled
        (0.5000, DOWN),
        (0.5625, UP),     # second release: tail begins again, from here
        (0.6250, UP),     # 1/16 s into the NEW tail (4/16 s into the old one)
        (0.6875, UP),     # exactly one tail after the second release -> STOP
    ])
    expected = [NONE, NONE, NONE, START, NONE, NONE, NONE, NONE, NONE, STOP]
    check(actions == expected, f"release-tail re-press: got {show(actions)}")
    check(actions.count(STOP) == 1, "the cancelled stop was emitted as well")


def check_foreign_ignored_while_recording():
    """Foreign keys and AltGr do not stop a running PTT recording.

    Dictating and typing at the same time is the intended behaviour; only releasing
    the trigger ends the recording.
    """
    det = new_detector()
    actions = play(det, [
        (0.0000, DOWN),
        (0.0625, UP),
        (0.1250, DOWN),
        (0.3125, DOWN),                     # START
        (0.3750, DOWN, False, True),        # typing while dictating
        (0.4375, DOWN, True),               # AltGr while dictating
        (0.5000, DOWN, True, True),
        (0.5625, UP),
        (0.6875, UP),                       # one tail after the release -> STOP
    ])
    expected = [NONE, NONE, NONE, START, NONE, NONE, NONE, NONE, STOP]
    check(actions == expected, f"foreign keys while recording: got {show(actions)}")


def check_reset():
    """reset() makes the detector inert without inventing a phantom press.

    The caller resets when a non-PTT recording takes over, i.e. typically while the
    trigger is still physically down. Keeping `_prev_trigger` is what stops the very
    next tick from reading that held key as a fresh rising edge.
    """
    det = new_detector()
    play(det, [(0.0000, DOWN), (0.0625, UP), (0.1250, DOWN), (0.3125, DOWN)])  # -> RECORDING
    det.reset()
    check(det.needs_foreign_scan() is False, "reset() did not return the detector to IDLE")

    after = play(det, [
        (0.3750, DOWN),   # trigger still down: no phantom edge, no re-arm
        (0.4375, UP),     # and no STOP for a recording the detector no longer owns
        (0.5000, UP),
    ])
    check(after == [NONE] * 3, f"after reset() in RECORDING: got {show(after)}")

    # Dead guard: a complete fresh gesture still works after a reset.
    revived = play(det, [
        (0.5625, DOWN),
        (0.6250, UP),
        (0.6875, DOWN),
        (0.8750, DOWN),
    ])
    check(revived == [NONE, NONE, NONE, START],
          f"a fresh gesture after reset() did not start: got {show(revived)}")

    # reset() out of ARMED: the pending double-tap is gone, not merely paused.
    det = new_detector()
    play(det, [(0.0000, DOWN), (0.0625, UP)])
    det.reset()
    resumed = play(det, [(0.1250, DOWN), (0.5000, DOWN)])
    check(resumed == [NONE, NONE], f"after reset() in ARMED: got {show(resumed)}")


def check_needs_foreign_scan():
    """The scan is requested exactly in the three arming states.

    `thoughtborne.py`'s `_ptt_tick` skips its foreign-key poll and hands the detector
    a hardcoded `foreign_down=False` whenever this returns False. So this predicate is
    not an optimisation detail: any arming state that joined the steady set would lose
    its foreign-key veto silently, with the detector unable to notice.
    """
    det = new_detector()
    stations = [
        (None, False, "IDLE"),
        ((0.0000, DOWN), True, "TAP_HELD"),
        ((0.0625, UP), True, "ARMED"),
        ((0.1250, DOWN), True, "HOLD_PENDING"),
        ((0.3125, DOWN), False, "RECORDING (start)"),
        ((0.3750, UP), False, "RECORDING (tail pending)"),
        ((0.5000, UP), False, "IDLE (after STOP)"),
    ]
    for step, expected, label in stations:
        if step is not None:
            play(det, [step])
        got = det.needs_foreign_scan()
        check(got is expected,
              f"needs_foreign_scan() in {label}: got {got}, expected {expected}")

    # The structural half of the same pin: which states are "steady" at all.
    # (Reaching into the private set is house practice for pinning an invariant the
    # public surface only shows indirectly.)
    check(set(ptt_detector._STEADY_STATES) == {ptt_detector._S.IDLE, ptt_detector._S.RECORDING},
          f"_STEADY_STATES changed: {ptt_detector._STEADY_STATES}")


def main():
    check_boundary_fixture_is_exact()
    check_happy_gesture()
    check_min_hold_boundary()
    check_tap_expiry()
    check_early_release()
    check_altgr_veto_disarms()
    check_foreign_veto_disarms()
    check_veto_special_case_foreign()
    check_veto_special_case_blocker()
    check_release_tail_repress()
    check_foreign_ignored_while_recording()
    check_reset()
    check_needs_foreign_scan()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: gesture timing boundaries, both vetoes, release tail, reset() and the "
          "foreign-scan pin all hold")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
