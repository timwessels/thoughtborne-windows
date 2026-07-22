#!/usr/bin/env python3
"""Off-Windows verification of the typed-insert length cap (#7, cites D-003).

`typed_cap` is pure/stdlib -- it imports nothing from the Windows-only stack --
so the character math, the notice construction, and its invariants (ASCII-only,
no newline, total payload <= cap) are checked on plain Python, where
`output_handler` (which loads Win32 DLLs at import) cannot run.

    python3 test_typed_cap.py    # verify, exit non-zero on any violation
"""
import sys

import typed_cap as tc
from typed_cap import cap_typed_text, TYPED_INSERT_CAP, TYPED_INSERT_CAP_NOTICE

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def main():
    cap = TYPED_INSERT_CAP
    notice = TYPED_INSERT_CAP_NOTICE.format(cap=cap)

    # The cap value is the decided one (D-003 / spike #161).
    check(cap == 4000, f"TYPED_INSERT_CAP is {cap}, expected 4000")

    # Notice invariants (it is *typed* into the target).
    check(all(ord(c) < 128 for c in notice), "notice is not pure ASCII")
    check("\n" not in notice, "notice contains a newline (would submit a form field)")
    check(not notice.startswith("\n"), "notice starts with a newline")
    check(notice.startswith(" "), "notice does not start with the separating space")
    check(f"{cap:,}" in notice, f"notice does not carry the formatted cap {cap:,}")
    check(len(notice) < cap, f"notice ({len(notice)}) is not shorter than the cap ({cap})")

    # Just under the cap: returned unchanged, not truncated.
    text = "x" * (cap - 1)
    to_type, truncated, original_len = cap_typed_text(text)
    check(to_type == text and truncated is False and original_len == cap - 1,
          "text one under the cap was altered or flagged truncated")

    # Exactly at the cap: <= so NOT truncated, returned unchanged.
    text = "x" * cap
    to_type, truncated, original_len = cap_typed_text(text)
    check(to_type == text and truncated is False and original_len == cap,
          "text exactly at the cap was altered or flagged truncated")

    # One over the cap: truncated, total == cap, notice at the end, body prefix intact.
    text = "x" * (cap + 1)
    to_type, truncated, original_len = cap_typed_text(text)
    check(truncated is True, "cap+1 was not flagged truncated")
    check(original_len == cap + 1, f"cap+1 original_len wrong: {original_len}")
    check(len(to_type) == cap, f"cap+1 typed length {len(to_type)} != cap {cap}")
    check(to_type.endswith(notice), "cap+1 typed text does not end with the notice")
    check(to_type.startswith("x" * (cap - len(notice))),
          "cap+1 body prefix is not the original text")

    # Far over the cap: same guarantees, real transcript-sized input.
    text = "a" * 40000
    to_type, truncated, original_len = cap_typed_text(text)
    check(truncated is True and original_len == 40000, "40k input flags wrong")
    check(len(to_type) == cap, f"40k typed length {len(to_type)} != cap {cap}")
    check(to_type.endswith(notice), "40k typed text does not end with the notice")
    check(to_type.startswith("a" * (cap - len(notice))), "40k body prefix wrong")
    check(len(to_type) <= cap, "40k typed payload exceeds the cap")

    # A non-default cap (above the ~170-char notice): the body math must follow
    # the parameter, not the constant.
    small = 500
    snotice = TYPED_INSERT_CAP_NOTICE.format(cap=small)
    check(len(snotice) < small, "the cap=500 fixture is mis-sized (notice >= cap)")
    to_type, truncated, original_len = cap_typed_text("y" * 2000, cap=small)
    check(truncated is True and original_len == 2000, "cap=500 flags wrong")
    check(len(to_type) == small, f"cap=500 typed length {len(to_type)} != 500")
    check(to_type.endswith(snotice), "cap=500 typed text does not end with its notice")

    # Pathological: a cap smaller than the notice -> guard path, no crash, len==cap.
    tiny = 10
    to_type, truncated, original_len = cap_typed_text("z" * 100, cap=tiny)
    check(truncated is True and original_len == 100, "cap=10 flags wrong")
    check(len(to_type) == tiny, f"cap=10 typed length {len(to_type)} != 10 (guard)")

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: typed_cap character math, notice invariants, and cap guard all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
