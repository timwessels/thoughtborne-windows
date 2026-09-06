#!/usr/bin/env python3
"""Off-Windows verification of the transcript cleanup functions (#242).

`_remove_spoken_fillers` (every engine) and `_clean_groq_hallucinations` (the
Groq path) rewrite the user's words before they are inserted, and both are pure
string functions: no audio, no network, no Win32. The only thing standing
between them and a plain-Python run is `transcriber`'s single third-party
module-level import (`groq`), which a stub satisfies where the SDK is absent,
and the API-key check in `__init__`, which `__new__` skips.

Pinned here is the documented behaviour: the #31/#97 filler removal with its
delimiter handling and sentence-start capital move, the #101 quote exception,
the word-boundary protection that keeps "Lähmung"/"ähnlich" intact, and the
Groq phrase/fragment strip with its length gate. Several cases are guards
rather than features -- they record where the removal deliberately does NOT
put punctuation back; a fix that "keeps the punctuation always" breaks exactly
those. On top of that baseline sit the two #242 fixes (a sentence's terminal
mark survives a filler at its boundary; the phrase list is re-checked once a
trailing fragment is gone) and two pins that a neutralized *call* would slip
past the cases above.

    python3 test_text_cleanup.py          # verify, exit non-zero on any violation
    python3 test_text_cleanup.py --show   # print every case as input -> output
"""
import inspect
import logging
import sys
import types

# transcriber imports groq at module level; this stub stands in where the SDK is
# absent. setdefault, never assignment: whoever imports groq first in a process
# owns the module for everyone after -- the real SDK when something already
# loaded it, and under pytest whichever driver was collected first. That sharing
# is why the stub mirrors the full one in test_retry_marker_lifecycle.py: a
# driver collected after this one must still find the exception hierarchy
# transcriber._groq_error_reason imports lazily (#138), whoever installed it.
_fake_groq = types.ModuleType("groq")
_fake_groq.Groq = type("Groq", (), {"__init__": lambda self, *a, **k: None})
class _FakeGroqError(Exception):
    pass
class _FakeGroqAPIError(_FakeGroqError):
    pass
class _FakeGroqAPIStatusError(_FakeGroqAPIError):
    def __init__(self, message="", *, status_code=None):
        self.status_code = status_code
class _FakeGroqAPIConnectionError(_FakeGroqAPIError):
    pass
class _FakeGroqAPITimeoutError(_FakeGroqAPIConnectionError):
    pass
class _FakeGroqAuthenticationError(_FakeGroqAPIStatusError):
    pass
class _FakeGroqRateLimitError(_FakeGroqAPIStatusError):
    pass
_fake_groq.AuthenticationError = _FakeGroqAuthenticationError
_fake_groq.APIStatusError = _FakeGroqAPIStatusError
_fake_groq.APIConnectionError = _FakeGroqAPIConnectionError
_fake_groq.APITimeoutError = _FakeGroqAPITimeoutError
_fake_groq.RateLimitError = _FakeGroqRateLimitError
sys.modules.setdefault("groq", _fake_groq)

logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

from transcriber import (  # noqa: E402
    AbstractTranscriber, GroqTranscriber, SonioxAsyncTranscriber, SonioxLiveTranscriber,
)

SHOW = "--show" in sys.argv

failures = []

# __new__ skips __init__ (and with it the API-key requirement); both methods
# under test only touch their argument.
_inst = GroqTranscriber.__new__(GroqTranscriber)
fillers = _inst._remove_spoken_fillers
groq_clean = _inst._clean_groq_hallucinations


def check(cond, msg):
    if not cond:
        failures.append(msg)


def run_table(label, fn, table):
    if SHOW:
        print(f"--- {label} ---")
    for src, want in table:
        got = fn(src)
        if SHOW:
            print(f"  {src!r}\n      -> {got!r}")
        check(got == want, f"{label}: {src!r} -> {got!r}, expected {want!r}")


def check_filler_baseline():
    """The shipped filler behaviour (#31/#97/#101), including the cases where
    it deliberately drops punctuation instead of putting it back."""
    run_table("filler baseline", fillers, [
        # Core removal: the filler plus the delimiter the model glued to it.
        ("Das ist, ähm, ein Test", "Das ist, ein Test"),
        ("Das ist ähm ein Test", "Das ist ein Test"),
        # A sentence-initial capitalized filler hands its capital on (#97).
        ("Ähm, das ist ein Test", "Das ist ein Test"),
        ("Äh, ähm, das ist ein Test", "Das ist ein Test"),
        ('SATZ." Ähm das geht', 'SATZ." Das geht'),
        ("Punkt: Ähm dann weiter", "Punkt: Dann weiter"),
        # Quoted fillers are the user's own words (#101).
        ('Er sagte "ähm".', 'Er sagte "ähm".'),
        ('Er sagte "ähm, dass die Pause wichtig ist".',
         'Er sagte "ähm, dass die Pause wichtig ist".'),
        # \b keeps the forms out of real words.
        ("Die Lähmung ist ähnlich gelagert", "Die Lähmung ist ähnlich gelagert"),
        # Trailing filler: the comma left dangling at the very end is trimmed.
        ("Das ist ein Test, ähm", "Das ist ein Test"),
        # Nothing but filler.
        ("ähm", ""),
        ("Ähm.", ""),
        # --- guards: no sentence punctuation may appear here ---
        # The kept text already ends the sentence; re-emitting would give "?." / ":.".
        ("Wirklich? Ähm. Also gut.", "Wirklich? Also gut."),
        ("Also: ähm.", "Also:"),
        ("Also: ähm. Und dann.", "Also: Und dann."),
        # ... including where a closing quote or bracket sits between that mark
        # and the filler (the corpus form _CAP_TRANSPARENT was built for): the
        # sentence is closed, so a second mark would land behind the quote.
        ('SATZ." Ähm. Und das geht.', 'SATZ." Und das geht.'),
        ("(Das war gut.) Ähm. Und dann.", "(Das war gut.) Und dann."),
        # Lowercase after the period -> the sentence did not end there.
        ("Das ist gut, ähm. und dann kam er.", "Das ist gut, und dann kam er."),
        ("Das war ähm. gut", "Das war gut"),
        # A digit is left to the same old path: a new sentence starting on a
        # number cannot be told from a mid-sentence one ("Das kostet, 3 Euro").
        ("Das ist gut, ähm. 3 Punkte folgen.", "Das ist gut, 3 Punkte folgen."),
        # A line break is the separator; an ender must not dangle in front of it.
        ("Erste Zeile\nähm. Und dann.", "Erste Zeile\nUnd dann."),
    ])


def check_groq_baseline():
    """The Groq end-artifact strip: phrase list, length gate, fragment list."""
    run_table("groq baseline", groq_clean, [
        ("Das ist der Inhalt. Vielen Dank.", "Das ist der Inhalt."),
        ("Das ist der Inhalt. Vielen Dank für Ihre Aufmerksamkeit.",
         "Das ist der Inhalt."),
        # len(cleaned) > 10 keeps a short transcript whole -- what is left
        # would be too little to be sure it was an artifact.
        ("Kurz. Danke.", "Kurz. Danke."),
        # The " und" fragment is the Whisper artifact the list was built for;
        # its behaviour is data, pinned as-is.
        ("Bitte kaufe Milch, Brot und", "Bitte kaufe Milch, Brot"),
        ("Inhalt hier steht. Also", "Inhalt hier steht."),
        ("", ""),
    ])


def check_filler_sentence_end_fix():
    """#242 fix 1: a filler at a sentence boundary no longer takes the
    sentence's terminal mark with it (and no longer leaves "!"/"?" orphaned).
    The welded form -- two sentences joined by a comma and a capital -- was the
    damaging one."""
    run_table("filler sentence end", fillers, [
        # At the end of the transcript the mark replaces the dangling comma.
        ("Das ist ein Test, ähm.", "Das ist ein Test."),
        ("Das ist ein Test, ähm!", "Das ist ein Test!"),
        # Mid-transcript: the two sentences stay two sentences.
        ("Das ist gut, ähm. Und dann kam er.", "Das ist gut. Und dann kam er."),
        ("Das ist gut, ähm? Oder nicht.", "Das ist gut? Oder nicht."),
        ("Das ist gut, ähm... Und dann.", "Das ist gut... Und dann."),
        ("Das ist gut; ähm. Und dann.", "Das ist gut. Und dann."),
        ("Das ist gut ähm. Und dann.", "Das ist gut. Und dann."),
        # A filler chain ("äh, ähm.") ends on the same boundary.
        ("Das ist gut, äh, ähm. Und dann.", "Das ist gut. Und dann."),
        ("Test ähm! Und dann.", "Test! Und dann."),
        # "!" and "?" now travel with the filler like "." always did: where the
        # gates do not fire they are dropped instead of left standing alone.
        ("Wirklich? Ähm! Also gut.", "Wirklich? Also gut."),
        ("Das ist gut, ähm! und weiter", "Das ist gut, und weiter"),
        ("Ähm!", ""),
    ])


def check_groq_phrase_after_fragment_fix():
    """#242 fix 2: with the trailing fragment gone, the phrase list is
    consulted once more -- it was the fragment that hid the phrase."""
    run_table("groq phrase after fragment", groq_clean, [
        ("Das ist der Inhalt. Vielen Dank und", "Das ist der Inhalt."),
        ("Das ist der Inhalt. Vielen Dank. Und", "Das ist der Inhalt."),
        ("Inhalt hier. Vielen Dank Das", "Inhalt hier."),
        # The length gate applies to the second sweep exactly as to the first:
        # what would be left is too short to be sure, so nothing more goes.
        ("Kurz und Danke und", "Kurz und Danke"),
    ])


def check_shared_method_identity():
    """Every engine runs the same filler removal (#97): one inherited method,
    no silent per-engine fork."""
    for cls in (GroqTranscriber, SonioxAsyncTranscriber, SonioxLiveTranscriber):
        check(cls._remove_spoken_fillers is AbstractTranscriber._remove_spoken_fillers,
              f"{cls.__name__} overrides _remove_spoken_fillers")


def check_callers_wired():
    """Source guard: neutralizing the functions is not the only way to lose
    them -- deleting the call has the same effect and no case above sees it."""
    for cls in (GroqTranscriber, SonioxAsyncTranscriber, SonioxLiveTranscriber):
        src = inspect.getsource(cls.transcribe)
        check("_remove_spoken_fillers(" in src,
              f"{cls.__name__}.transcribe no longer calls _remove_spoken_fillers")
    check("_clean_groq_hallucinations(" in inspect.getsource(GroqTranscriber.transcribe),
          "GroqTranscriber.transcribe no longer calls _clean_groq_hallucinations")


def main():
    check_filler_baseline()
    check_groq_baseline()
    check_filler_sentence_end_fix()
    check_groq_phrase_after_fragment_fix()
    check_shared_method_identity()
    check_callers_wired()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: filler removal, sentence-end punctuation, Groq artifact stripping "
          "and the wiring all behave as documented")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
