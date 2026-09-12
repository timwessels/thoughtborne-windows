#!/usr/bin/env python3
"""Off-Windows verification of the mouse-hotkey press-edge rules (#308).

`mouse_detector` is the second deliberately Win32-decoupled module beside
`ptt_detector`: it takes one down/up reading per watched virtual key per tick and
returns the hotkey ids to fire. That is what makes this lane checkable at all off
Windows -- nothing else about it is. The Win32 half (the `GetAsyncKeyState` read
through `hotkey_manager.is_vk_pressed`, the 10 ms clock of
`thoughtborne.mouse_hotkey_thread`, and the `PostThreadMessageW` delivery) has no
Linux equivalent and is verified in daily use.

Every case compares the EXACT list of fired ids per tick, not "it fired
somewhere": the failures this driver exists to catch -- a hotkey that repeats for
every tick a button is held, a press invented on the very first tick -- all fire
the right id, just at the wrong tick or too often.

What is pinned:

  - the priming tick. The detector is built while the user's hand is wherever it
    is, so a button already down when it comes into existence must not read as a
    press just made -- the same discipline `ptt_detector` keeps with
    `_prev_trigger`, and the reason `reset()` there does not clear it either;
  - the rising edge, exactly once per press, whatever happens in between: a held
    button fires once however long it stays down, and the next press needs a
    release first;
  - that only the high bit is consulted. The edge is built from successive
    physical-down reads plus the detector's own memory -- the rule the 2026-09-08
    Win32 study settles on, because `GetAsyncKeyState`'s "pressed since the last
    call" bit is consumed by whoever queries first. Nothing here may reconstruct
    an edge from anything else;
  - independence and order: two bound buttons neither interfere nor swap places,
    and the ids come back in binding order -- the order they are posted in;
  - the inert shapes -- no bindings at all, and a reading the caller could not
    take -- which must be no-ops rather than errors, since the caller is a poll
    thread whose fault would cost the whole lane.

    python3 test_mouse_detector.py    # verify, exit non-zero on any violation
"""
import sys

from mouse_detector import MouseEdgeDetector

# The two VKs the real wiring watches most (XBUTTON1/XBUTTON2); the detector
# never interprets them, so any two distinct ints would do.
VK_X1 = 0x05
VK_X2 = 0x06

DOWN = True
UP = False

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def play(bindings, ticks):
    """Drive a fresh detector through `ticks` -> the fired ids, one list per tick."""
    det = MouseEdgeDetector(bindings)
    return [det.tick(t) for t in ticks]


def check_priming_tick_fires_nothing():
    """A button already held when the detector is built is not a fresh press."""
    for first in (DOWN, UP):
        got = play([(VK_X1, 7)], [{VK_X1: first}])
        check(got == [[]], f"priming tick with down={first} fired {got}, expected [[]]")
    # The press AFTER priming is a real one, even when the first tick saw a held
    # button: the release in between is what makes it new.
    got = play([(VK_X1, 7)], [{VK_X1: DOWN}, {VK_X1: UP}, {VK_X1: DOWN}])
    check(got == [[], [], [7]], f"press after a primed hold: {got}")
    # ... and a button held from before the detector existed never fires at all
    # while it stays down, however many ticks pass.
    got = play([(VK_X1, 7)], [{VK_X1: DOWN}] * 5)
    check(got == [[]] * 5, f"hold that predates the detector: {got}")


def check_rising_edge_fires_once():
    """One press, one hit -- silence while it is held, and the next press needs a
    release first. This is the whole rule; everything else is bookkeeping."""
    got = play([(VK_X1, 3)], [{VK_X1: UP},      # prime
                              {VK_X1: DOWN},    # press
                              {VK_X1: DOWN},    # held
                              {VK_X1: DOWN},    # still held
                              {VK_X1: UP},      # released
                              {VK_X1: DOWN}])   # pressed again
    check(got == [[], [3], [], [], [], [3]], f"rising edges: {got}")
    # A long hold is one hit, not a repeat -- the failure a user would notice
    # first, since the hotkey would fire ~100 times a second.
    got = play([(VK_X1, 3)], [{VK_X1: UP}] + [{VK_X1: DOWN}] * 20)
    check(got == [[]] + [[3]] + [[]] * 19, f"20-tick hold: {got}")


def check_two_buttons_are_independent_and_ordered():
    """Two bindings do not interfere, and a tick that fires both returns them in
    binding order -- the order the poll thread then posts them in."""
    bindings = [(VK_X1, 1), (VK_X2, 2)]
    got = play(bindings, [{VK_X1: UP, VK_X2: UP},
                          {VK_X1: DOWN, VK_X2: UP},
                          {VK_X1: DOWN, VK_X2: DOWN},
                          {VK_X1: UP, VK_X2: UP},
                          {VK_X1: DOWN, VK_X2: DOWN}])
    check(got == [[], [1], [2], [], [1, 2]], f"two buttons: {got}")
    # The reverse binding order reverses the output, so the assertion above is
    # about order and not about the ids happening to sort that way.
    got = play([(VK_X2, 2), (VK_X1, 1)],
               [{VK_X1: UP, VK_X2: UP}, {VK_X1: DOWN, VK_X2: DOWN}])
    check(got == [[], [2, 1]], f"reversed binding order: {got}")


def check_watched_vks_is_the_binding_order():
    """What the caller reads each tick, and in which order -- the poll thread
    builds its readings dict straight from this, once, for its whole life."""
    det = MouseEdgeDetector([(VK_X2, 2), (VK_X1, 1)])
    check(det.watched_vks == [VK_X2, VK_X1], f"watched_vks: {det.watched_vks}")
    check(MouseEdgeDetector([]).watched_vks == [], "empty bindings must watch nothing")


def check_inert_shapes():
    """No bindings, and a missing reading: both no-ops, never an error. The caller
    is a poll thread where an exception ends the whole mouse lane."""
    got = play([], [{}, {VK_X1: DOWN}])
    check(got == [[], []], f"no bindings: {got}")
    # A vk the caller could not read counts as up rather than raising -- and a
    # missing reading must never be the thing that invents a press.
    got = play([(VK_X1, 4)], [{}, {}, {VK_X1: DOWN}])
    check(got == [[], [], [4]], f"missing reading: {got}")
    got = play([(VK_X1, 4)], [{VK_X1: UP}, {VK_X1: DOWN}, {}, {VK_X1: DOWN}])
    check(got == [[], [4], [], [4]], f"reading lost mid-hold: {got}")


def check_edge_rests_on_the_high_bit_alone():
    """The 2026-09-08 study's rule, as a structural assertion: the detector is fed
    one boolean per key and keeps its own previous value -- there is no second
    input it could reconstruct an edge from, and no per-key state beyond that.

    (Reaching into the private attribute is house practice for pinning an
    invariant the public surface only shows indirectly; test_ptt_detector does the
    same with _STEADY_STATES.)"""
    det = MouseEdgeDetector([(VK_X1, 1)])
    det.tick({VK_X1: UP})
    det.tick({VK_X1: DOWN})
    check(det._down == {VK_X1: True}, f"remembered state: {det._down}")
    # One boolean in, one boolean remembered: a tick fed the same reading twice
    # cannot distinguish the two, which is what makes "it can never invent a
    # press" true regardless of what Windows' low bit is doing.
    check(det.tick({VK_X1: DOWN}) == [], "a repeated down reading must not fire")


def main():
    check_priming_tick_fires_nothing()
    check_rising_edge_fires_once()
    check_two_buttons_are_independent_and_ordered()
    check_watched_vks_is_the_binding_order()
    check_inert_shapes()
    check_edge_rests_on_the_high_bit_alone()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: priming, one hit per press however long it is held, binding order, "
          "the inert shapes and the high-bit-only edge all hold")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
