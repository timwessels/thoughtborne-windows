"""Length cap for the typed insert path (keyboard.write / SendInput), #7.

keyboard.write() injects each character via Win32 SendInput without pausing; past
an app-dependent break point the target's input queue overflows and silently drops
~80% of the remaining characters, in order, while reporting full success (spike
#161, _research/2026-07_typed-insert-drops/). We do not repair this (no pacing --
the app drain rate is unknown and per-app); we cap the typed text below the
observed break point and append an honest notice. Nothing is lost: the full
transcript stays in history/ and is re-insertable via the clipboard hotkey.

Pure/stdlib so it imports and is tested off Windows (output_handler cannot -- it
loads Win32 DLLs at import). See DECISIONS.md D-003 and AGENTS.md.
"""

TYPED_INSERT_CAP = 4000  # chars; see D-003 / spike #161 (5,897 landed whole, broke at 6,292)

# Appended to a truncated typed insert. ASCII only (it is *typed* into the target,
# so no em dash / smart quotes) and it starts with a plain space, never a newline:
# a newline would arrive as Enter via SendInput and could submit a single-line
# form field, and typing targets are often exactly such fields. {cap} is
# thousands-formatted at build time.
TYPED_INSERT_CAP_NOTICE = (
    " [Typed insert capped at {cap:,} characters -- the rest was not typed. "
    "The full transcript is in Thoughtborne's history folder and can be inserted "
    "with the clipboard hotkey.]"
)


def cap_typed_text(text, cap=TYPED_INSERT_CAP):
    """Return (text_to_type, truncated, original_len).

    - len(text) <= cap: returns text unchanged, truncated=False.
    - len(text) >  cap: returns capped_body + notice with total length == cap
      (so the whole typed payload, body + notice, is guaranteed <= cap),
      truncated=True. original_len is always len(text).
    """
    original_len = len(text)
    if original_len <= cap:
        return text, False, original_len
    notice = TYPED_INSERT_CAP_NOTICE.format(cap=cap)
    if len(notice) >= cap:                     # pathological: notice alone >= cap
        return text[:cap], True, original_len  # guard only; never with 4000 + ~170
    body = text[:cap - len(notice)]
    return body + notice, True, original_len
