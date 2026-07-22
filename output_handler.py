"""
Output Handler Module (Windows version)

This module manages text output and clipboard operations including:
- Sequential text output queue management
- Keyboard and clipboard text insertion
- Thread-safe output coordination
- Transcript history management
- Send-after-insert functionality (press Enter to send messages)
- Auto-insert toggle (process only, insert later)

Windows Adaptations:
- Uses keyboard module for keyboard.write() and keyboard.send() (text output)
- Ctrl+V instead of Cmd+V for clipboard insertion
- Active modifier key polling with GetAsyncKeyState via is_key_pressed() (hook-free)

Classes:
    TranscriptionTask: Data class for transcription tasks
    OutputManager: Manages the output queue and text insertion
"""

import time
import queue
import logging
import threading
import ctypes
from ctypes import wintypes
import keyboard
import pyperclip
from hotkey_manager import is_key_pressed
import pyautogui
from dataclasses import dataclass
from typing import Optional, Dict, Deque, List
from collections import deque

from config import (
    TRANSCRIPT_HISTORY_SIZE, OUTPUT_QUEUE_TIMEOUT,
    CLIPBOARD_RESTORE_DELAY, KEY_RELEASE_DELAY, FILE_ONLY
)

logger = logging.getLogger('Thoughtborne.OutputHandler')


# ===== CLIPBOARD/FOCUS DIAGNOSTICS (#29) =====
# Observation only, DEBUG/file-only. Private WinDLL instances so the
# argtypes/restype below can't interfere with the cached ctypes.windll
# function objects that pyperclip / keyboard / pyautogui configure.
_diag_user32 = ctypes.WinDLL("user32", use_last_error=True)
_diag_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_diag_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_diag_user32.GetClipboardOwner.restype = wintypes.HWND
_diag_user32.GetOpenClipboardWindow.restype = wintypes.HWND
_diag_user32.GetForegroundWindow.restype = wintypes.HWND
_diag_user32.CountClipboardFormats.restype = ctypes.c_int
_diag_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_diag_user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_diag_user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_diag_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_diag_kernel32.OpenProcess.restype = wintypes.HANDLE
_diag_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_diag_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)
]
_diag_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

# Formats worth distinguishing: text vs image (BITMAP/DIB synthesized pair)
# vs copied files (HDROP)
_DIAG_CF_CHECKS = ((13, "UNICODETEXT"), (2, "BITMAP"), (8, "DIB"), (15, "HDROP"))

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _describe_hwnd(hwnd) -> str:
    """Best-effort 'class|title|process' description of a window handle.

    Cross-process GetWindowTextW reads the cached title (no SendMessage),
    so this cannot hang on an unresponsive window.
    """
    if not hwnd:
        return "none"
    try:
        cls_buf = ctypes.create_unicode_buffer(64)
        _diag_user32.GetClassNameW(hwnd, cls_buf, 64)
        title_buf = ctypes.create_unicode_buffer(64)
        _diag_user32.GetWindowTextW(hwnd, title_buf, 64)
        pid = wintypes.DWORD()
        _diag_user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = "?"
        hproc = _diag_kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if hproc:
            try:
                path_buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(260)
                if _diag_kernel32.QueryFullProcessImageNameW(
                    hproc, 0, path_buf, ctypes.byref(size)
                ):
                    exe = path_buf.value.rsplit("\\", 1)[-1]
            finally:
                _diag_kernel32.CloseHandle(hproc)
        title = title_buf.value[:30]
        return f"[{cls_buf.value}|{title}|{exe}]"
    except Exception:
        return "[?]"


def _clipboard_diag(tag: str) -> None:
    """Log one clipboard/focus snapshot line. Never raises.

    All queries are non-invasive: none of them open the clipboard, so the
    diagnostics cannot themselves contribute to an ownership race.
    """
    try:
        seq = _diag_user32.GetClipboardSequenceNumber()
        owner = _describe_hwnd(_diag_user32.GetClipboardOwner())
        holder = _describe_hwnd(_diag_user32.GetOpenClipboardWindow())
        fg = _describe_hwnd(_diag_user32.GetForegroundWindow())
        fmt_count = _diag_user32.CountClipboardFormats()
        fmts = ",".join(
            name for cf, name in _DIAG_CF_CHECKS
            if _diag_user32.IsClipboardFormatAvailable(cf)
        )
        logger.debug(
            f"[CLIPDIAG {tag}] seq={seq} formats={fmt_count}({fmts}) "
            f"owner={owner} held_open_by={holder} foreground={fg}"
        )
    except Exception as e:
        logger.debug(f"[CLIPDIAG {tag}] diagnostics failed: {e}")


def _diag_sample_window(label: str, duration_seconds: float) -> None:
    """Busy-sample holder/foreground/seq for `duration_seconds` in ~5 ms steps,
    logging changes only.

    Round-1 diagnostics showed clean snapshots at every fixed measuring point
    even on failing pastes, so whatever interferes must be transient within
    the windows between them. Effective resolution is bounded by the OS sleep
    granularity (~1-15 ms); total duration is kept exact via the deadline.
    """
    try:
        prev_holder = _diag_user32.GetOpenClipboardWindow()
        prev_fg = _diag_user32.GetForegroundWindow()
        prev_seq = _diag_user32.GetClipboardSequenceNumber()
    except Exception:
        time.sleep(duration_seconds)
        return
    start = time.perf_counter()
    deadline = start + duration_seconds
    changes = 0
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        time.sleep(min(0.005, remaining))
        try:
            holder = _diag_user32.GetOpenClipboardWindow()
            fg = _diag_user32.GetForegroundWindow()
            seq = _diag_user32.GetClipboardSequenceNumber()
        except Exception:
            break
        offset_ms = (time.perf_counter() - start) * 1000
        if holder != prev_holder:
            logger.debug(
                f"[CLIPDIAG {label} +{offset_ms:.0f}ms] "
                f"holder -> {_describe_hwnd(holder)}"
            )
            prev_holder = holder
            changes += 1
        if fg != prev_fg:
            logger.debug(
                f"[CLIPDIAG {label} +{offset_ms:.0f}ms] "
                f"foreground -> {_describe_hwnd(fg)}"
            )
            prev_fg = fg
            changes += 1
        if seq != prev_seq:
            logger.debug(
                f"[CLIPDIAG {label} +{offset_ms:.0f}ms] seq {prev_seq} -> {seq}"
            )
            prev_seq = seq
            changes += 1
    if changes == 0:
        logger.debug(
            f"[CLIPDIAG {label}] no holder/foreground/seq change "
            f"in {duration_seconds * 1000:.0f}ms window"
        )


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


_diag_user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO)]
_diag_user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
]
_diag_user32.SendMessageTimeoutW.restype = ctypes.c_size_t

_WM_GETTEXTLENGTH = 0x000E
_SMTO_ABORTIFHUNG = 0x0002


def _diag_focus_window():
    """hwnd that actually has keyboard focus in the foreground GUI thread.

    GetForegroundWindow only names the top-level window; the keystroke lands
    in this child (e.g. the Scintilla control inside Notepad++).
    """
    try:
        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if _diag_user32.GetGUIThreadInfo(0, ctypes.byref(info)):
            return info.hwndFocus
    except Exception:
        pass
    return None


def _diag_text_length(hwnd):
    """Text length of a window via WM_GETTEXTLENGTH, or None.

    SendMessageTimeout with SMTO_ABORTIFHUNG so an unresponsive target
    cannot stall the output thread. Edit controls, RichEdit and Scintilla
    all answer this; before/after comparison is ground truth for whether
    a paste actually landed (#29).
    """
    if not hwnd:
        return None
    try:
        result = ctypes.c_size_t(0)
        ok = _diag_user32.SendMessageTimeoutW(
            hwnd, _WM_GETTEXTLENGTH, 0, 0, _SMTO_ABORTIFHUNG, 100,
            ctypes.byref(result),
        )
        if not ok:
            return None
        return result.value
    except Exception:
        return None


def _window_class(hwnd) -> str:
    """Window class name, or '' if unavailable."""
    if not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(64)
        _diag_user32.GetClassNameW(hwnd, buf, 64)
        return buf.value
    except Exception:
        return ""


# ===== NOTEPAD++ STALE-PASTE-GATE WORKAROUND (#29) =====
# Notepad++ caches its paste-enabled state (checkClipboard -> SCI_CANPASTE ->
# enableCommand(IDM_EDIT_PASTE)) and registers no clipboard listener. With an
# image or copied file on the clipboard the cached state is "paste disabled";
# our pyperclip text copy goes unnoticed, and since Ctrl+V is an accelerator
# on that menu command, Win32 swallows the keystroke entirely (disabled menu
# item => no WM_COMMAND, no beep). Known upstream as notepad-plus-plus#16456
# (internal copy/cut only, v8.8); the external-clipboard case is #18118, fixed
# upstream by a clipboard listener in 331ace4f (not yet released) — this nudge
# stays until that ships and propagates (removal tracked in thoughtborne #71).
# Full analysis with sources: _research/2026-06_npp-paste-gate-clipboard/.
#
# The cure N++ itself uses on window activation: SCI_SETXOFFSET triggers an
# unconditional SCN_UPDATEUI, whose handler re-runs checkClipboard(). Setting
# the offset to its current value is therefore a side-effect-free nudge that
# re-enables paste now that our text is on the clipboard.

_SCI_SETXOFFSET = 2397
_SCI_GETXOFFSET = 2398

# Classes whose WM_GETTEXTLENGTH answer reflects the real document, making
# "length unchanged" trustworthy evidence that a paste did not land. Custom-
# rendered targets (browsers etc.) fall through to DefWindowProc and answer
# with the (constant) window-title length — never retry on those, a landed
# paste would be invisible and the retry would paste twice.
_TEXT_CONTROL_CLASSES = frozenset({
    "Scintilla", "Edit", "RichEdit20W", "RichEdit20A", "RICHEDIT50W",
    "RichEditD2DPT",
})


def _nudge_scintilla_updateui(hwnd) -> None:
    """Re-trigger the target Scintilla's SCN_UPDATEUI (hang-safe, no-op on error)."""
    try:
        offset = ctypes.c_size_t(0)
        ok = _diag_user32.SendMessageTimeoutW(
            hwnd, _SCI_GETXOFFSET, 0, 0, _SMTO_ABORTIFHUNG, 100,
            ctypes.byref(offset),
        )
        if not ok:
            logger.debug("Scintilla nudge skipped: SCI_GETXOFFSET unanswered")
            return
        dummy = ctypes.c_size_t(0)
        _diag_user32.SendMessageTimeoutW(
            hwnd, _SCI_SETXOFFSET, offset.value, 0, _SMTO_ABORTIFHUNG, 100,
            ctypes.byref(dummy),
        )
        logger.debug(
            f"Scintilla nudge sent (SCI_SETXOFFSET {offset.value}) to "
            f"{_describe_hwnd(hwnd)} so the target re-checks the clipboard (#29)"
        )
    except Exception as e:
        logger.debug(f"Scintilla nudge failed: {e}")


@dataclass
class TranscriptionTask:
    """Represents a transcription task in the output queue"""
    sequence_number: int
    timestamp: str
    transcript: Optional[str] = None
    is_complete: bool = False
    is_error: bool = False
    is_immediate: bool = False  # For "insert last text" functionality
    use_clipboard: bool = False  # Whether to use clipboard for insertion
    wait_for_key_release: bool = False  # Whether to wait for key release before inserting
    trigger_keys: Optional[List[str]] = None  # Which keys to wait for
    auto_insert: bool = True  # Whether to automatically insert text (False = only save for later)
    send_after_insert: bool = False  # Whether to press Enter after inserting (for sending messages)
    no_speech: bool = False  # Empty on every engine -> honest "no speech" verdict, not a failure (#133)
    error_reason: Optional[str] = None  # Coarse failure category on a FAILED task (#138); rendered by #159
    error_provider: Optional[str] = None  # Short engine family that produced the failure (#159): "Soniox"/"Groq"
    error_inconclusive: bool = False  # Soniox-Live V2->V4 lane ran empty with >=1 errored stage, minus a conclusive auth reject -> "came back empty, worth a retry" (#159)


class OutputManager:
    """Manages sequential text output and clipboard operations (Windows version)"""

    def __init__(self, on_task_complete_callback=None):
        """
        Initialize the output manager

        Args:
            on_task_complete_callback: Optional event callback, invoked from the
                output thread when a task finishes (#37). Keyword contract:
                  callback(event='inserted', seq=..., chars=..., sent=...)
                      -- transcript inserted; sent=True when Enter was pressed
                         afterwards (the send flow)
                  callback(event='ready', seq=..., chars=...)
                      -- Y flow: processed but NOT inserted, waiting for the user
                  callback(event='failed', kind='transcription'|'insertion', seq=...)
                      -- nothing was inserted; a transcription failure also carries
                         reason=<coarse category #138> / provider=<"Soniox"|"Groq">
                         / inconclusive=<Soniox-Live empty-lane, #159>, so the
                         FAILED panel can say why (all None/False on an
                         uncategorized failure)
                Must not block: it runs inline in the sequential output loop.
        """
        self.output_queue: Dict[int, TranscriptionTask] = {}
        self.output_queue_lock = threading.Lock()
        self.output_condition = threading.Condition(self.output_queue_lock)
        self.on_task_complete = on_task_complete_callback

        self.next_sequence_to_output = 0
        self.sequence_counter = 0
        self.sequence_lock = threading.Lock()

        # Separate counter for immediate tasks (negative sequence numbers)
        self.immediate_counter = 0
        self.immediate_lock = threading.Lock()

        self.last_transcript = ""
        self.transcript_lock = threading.Lock()

        self.transcript_history: Deque[str] = deque(maxlen=TRANSCRIPT_HISTORY_SIZE)
        self.history_lock = threading.Lock()

        self.keyboard_lock = threading.Lock()
        self.running = True

        # Start output manager thread
        self.output_thread = threading.Thread(
            target=self._output_manager_thread,
            daemon=True,
            name="OutputManager"
        )
        self.output_thread.start()
        logger.info("Output manager initialized and started (Windows version)", extra=FILE_ONLY)

    def get_next_sequence_number(self) -> int:
        """Get the next sequence number (thread-safe)"""
        with self.sequence_lock:
            current = self.sequence_counter
            self.sequence_counter += 1
            return current

    def get_next_immediate_sequence_number(self) -> int:
        """Get the next immediate sequence number (negative, thread-safe)"""
        with self.immediate_lock:
            self.immediate_counter -= 1
            return self.immediate_counter

    def add_task(self, task: TranscriptionTask):
        """Add a task to the output queue"""
        with self.output_condition:
            self.output_queue[task.sequence_number] = task
            self.output_condition.notify()

    def update_last_transcript(self, text: str):
        """Update the last transcript (thread-safe)"""
        with self.transcript_lock:
            self.last_transcript = text

        with self.history_lock:
            self.transcript_history.append(text)

    def get_last_transcript(self) -> str:
        """Get the last transcript (thread-safe)"""
        with self.transcript_lock:
            return self.last_transcript

    def insert_last_transcript(self, use_clipboard: bool = False, wait_for_keys: List[str] = None, send_after_insert: bool = False):
        """Insert the last transcript via the queue"""
        transcript = self.get_last_transcript()

        if transcript:
            mode = "Clipboard" if use_clipboard else "Keyboard"
            send_info = " + Send" if send_after_insert else ""
            # Use negative sequence number for immediate tasks
            seq_num = self.get_next_immediate_sequence_number()
            logger.debug(f"Inserting last text via queue (Seq: {seq_num}, Length: {len(transcript)}, Mode: {mode}{send_info})")

            task = TranscriptionTask(
                sequence_number=seq_num,
                timestamp=time.strftime('%Y%m%d_%H%M%S'),
                transcript=transcript,
                is_complete=True,
                is_immediate=True,
                use_clipboard=use_clipboard,
                wait_for_key_release=(wait_for_keys is not None),
                trigger_keys=wait_for_keys,
                send_after_insert=send_after_insert
            )
            self.add_task(task)

    def _ensure_no_modifiers_pressed(self, max_wait=2.0):
        """
        Wait until no modifier keys are pressed (Windows version)

        Does NOT send any key events - only waits for the user to release naturally.
        Uses GetAsyncKeyState via is_key_pressed() to poll modifier state (hook-free).

        Args:
            max_wait: Maximum time to wait in seconds (default: 2.0)

        Returns:
            True if no modifiers pressed, False if timeout
        """
        start_time = time.time()

        while time.time() - start_time < max_wait:
            modifiers_pressed = False

            for key in ['ctrl', 'alt', 'shift', 'win']:
                if is_key_pressed(key):
                    modifiers_pressed = True
                    logger.debug(f"Modifier {key} still pressed, waiting...")
                    break

            if not modifiers_pressed:
                return True

            time.sleep(0.05)

        logger.warning("Timeout waiting for modifier keys to be released")
        return False

    def _paste_via_hotkey(self):
        """
        Send Ctrl+V to paste clipboard content into the currently active application.

        Key insight: We DON'T need to switch apps!
        - User works in their editor/app
        - User presses the paste hotkey
        - Hotkey is detected even if terminal is in background
        - Script transcribes in background
        - User's app REMAINS the active app the whole time
        - We just send Ctrl+V to the active app

        Returns:
            True if successful, False otherwise
        """
        try:
            time.sleep(0.05)

            # Send Ctrl+V to the currently active application
            logger.debug("About to send Ctrl+V to active application")

            with self.keyboard_lock:
                keyboard.send('ctrl+v')
                logger.debug("Ctrl+V sent")

            return True

        except Exception as e:
            logger.error(f"Error sending Ctrl+V: {e}", exc_info=True)
            return False

    def _insert_text_via_clipboard(self, text: str) -> bool:
        """
        Insert text via clipboard with original content restoration (Windows version)

        Args:
            text: Text to insert

        Returns:
            True if successful, False otherwise
        """
        try:
            # Wait until user has released all modifier keys
            if not self._ensure_no_modifiers_pressed():
                logger.error("Cannot insert via clipboard - modifiers still pressed after timeout")
                return False

            _clipboard_diag("pre-read")

            # Save current clipboard content
            original_clipboard = None
            clipboard_had_non_text = False
            try:
                original_clipboard = pyperclip.paste()
                if original_clipboard is None or original_clipboard == "":
                    clipboard_had_non_text = True
                    logger.debug("Empty clipboard detected - might be non-text content")
                else:
                    logger.debug(f"Clipboard content saved ({len(original_clipboard)} chars)")
            except Exception as e:
                logger.warning(f"Could not read clipboard (might contain image): {e}")
                clipboard_had_non_text = True

            # Copy text to clipboard
            pyperclip.copy(text)
            logger.debug(f"Text copied to clipboard ({len(text)} chars): '{text[:50]}{'...' if len(text) > 50 else ''}'")
            _clipboard_diag("post-copy")

            # Stale-paste-gate workaround (#29): after non-text content the
            # target may still cache "paste disabled" and swallow our Ctrl+V.
            # Nudge it before the wait below, so its UpdateUI runs during it.
            focus_hwnd = _diag_focus_window()
            focus_class = _window_class(focus_hwnd)
            if clipboard_had_non_text and focus_class == "Scintilla":
                _nudge_scintilla_updateui(focus_hwnd)

            # Delay to ensure clipboard is updated - longer if non-text was in clipboard
            if clipboard_had_non_text:
                logger.debug("Non-text clipboard detected, using longer delay for compatibility")
                _diag_sample_window("pre-paste-wait", 0.2)  # 200ms for apps like Notepad++ to recognize change
            else:
                _diag_sample_window("pre-paste-wait", 0.1)

            # Verify clipboard content
            try:
                clipboard_check = pyperclip.paste()
                if clipboard_check == text:
                    logger.debug(f"Clipboard verified: content matches ({len(clipboard_check)} chars)")
                else:
                    logger.warning(f"Clipboard mismatch! Expected {len(text)} chars, got {len(clipboard_check)} chars")
            except Exception as e:
                logger.warning(f"Could not verify clipboard: {e}")

            _clipboard_diag("pre-paste")

            # Ground truth for #29: text length of the focused control before
            # and after the paste tells whether it actually landed.
            len_before = _diag_text_length(focus_hwnd)
            logger.debug(
                f"[CLIPDIAG focus] {_describe_hwnd(focus_hwnd)} "
                f"text_length_before={len_before}"
            )

            # Send Ctrl+V to the ACTIVE application
            paste_success = self._paste_via_hotkey()

            if not paste_success:
                logger.warning("Paste via hotkey failed")
                return False

            # Delay after insertion
            _diag_sample_window("post-paste", CLIPBOARD_RESTORE_DELAY)
            _clipboard_diag("post-paste")

            # Verified single retry (#29): only on the non-text path (the
            # text path has never been observed to fail), only for control
            # classes whose WM_GETTEXTLENGTH answer is trustworthy, only when
            # the length provably did not change, and only while the focus is
            # still on the same control. Residual double-paste risk is
            # confined to two narrow cases: a paste that replaced a selection
            # of exactly equal length, and a target in a nested message loop
            # that answers sent messages while the V keydown still waits in
            # its input queue. Must run BEFORE the restore below, while the
            # clipboard still holds the transcript.
            len_after = _diag_text_length(focus_hwnd)
            if (
                clipboard_had_non_text
                and focus_class in _TEXT_CONTROL_CLASSES
                and len_before is not None
                and len_after == len_before
            ):
                # Re-measure once before retrying: a busy target may process
                # the paste a moment after our first measurement.
                time.sleep(0.25)
                len_after = _diag_text_length(focus_hwnd)
                if len_after == len_before and _diag_focus_window() == focus_hwnd:
                    logger.warning(
                        f"Paste did not land in {focus_class} target "
                        f"(text length unchanged at {len_before}); "
                        f"nudging and retrying once"
                    )
                    if focus_class == "Scintilla":
                        _nudge_scintilla_updateui(focus_hwnd)
                        time.sleep(0.1)
                    # A failed retry send is deliberately not propagated: the
                    # first Ctrl+V did go out, so the function's contract
                    # ("insertion attempted") still holds; the verdict below
                    # records the outcome either way.
                    paste_success = self._paste_via_hotkey()
                    if paste_success:
                        time.sleep(CLIPBOARD_RESTORE_DELAY)
                        len_after = _diag_text_length(focus_hwnd)

            # Restore prior clipboard only when real text was saved; an empty
            # string means non-text/empty content, and copy("") would wipe the
            # transcript ~100 ms after Ctrl+V before it lands (#23).
            if original_clipboard:
                try:
                    pyperclip.copy(original_clipboard)
                    logger.debug("Original clipboard content restored")
                except Exception as e:
                    logger.warning(f"Could not restore clipboard: {e}")

            # #29 diagnosis window: keep watching while the target app may
            # still be processing the paste, then record the verdict.
            _diag_sample_window("post-restore", 0.4)
            if len_before is None or len_after is None:
                logger.debug(
                    f"[CLIPDIAG verdict] text length unavailable "
                    f"(before={len_before}, after={len_after})"
                )
            elif focus_class not in _TEXT_CONTROL_CLASSES:
                # WM_GETTEXTLENGTH outside the trusted classes falls through
                # to DefWindowProc, which answers with the window-title
                # length — so the delta is meaningless as paste evidence
                # here, regardless of its sign. Record the raw numbers for
                # forensics but don't pretend we know whether it landed
                # (#33).
                final_len = _diag_text_length(focus_hwnd)
                if final_len is not None:
                    len_after = final_len
                delta = len_after - len_before
                logger.debug(
                    f"[CLIPDIAG verdict] text_length {len_before} -> {len_after} "
                    f"(delta {delta:+d}, pasted text was {len(text)} chars): "
                    f"INCONCLUSIVE (untrusted length source)"
                )
            else:
                final_len = _diag_text_length(focus_hwnd)
                if final_len is not None:
                    len_after = final_len
                delta = len_after - len_before
                # A negative delta means the paste replaced a longer
                # selection — that is a landed paste, not a failure.
                verdict = "PASTE LANDED" if delta != 0 else "PASTE DID NOT LAND"
                # Untrusted classes were routed to the elif above; this
                # branch is trusted-only, so a zero delta is a genuine miss.
                log = logger.warning if delta == 0 else logger.debug
                log(
                    f"[CLIPDIAG verdict] text_length {len_before} -> {len_after} "
                    f"(delta {delta:+d}, pasted text was {len(text)} chars): {verdict}"
                )

            return True

        except Exception as e:
            logger.error(f"Error during clipboard insertion: {e}")
            # Fallback to keyboard.write
            try:
                logger.info("Trying fallback to keyboard.write()", extra=FILE_ONLY)
                with self.keyboard_lock:
                    keyboard.write(text)
                return True
            except Exception as e2:
                logger.error(f"Fallback also failed: {e2}")
                return False

    def _output_manager_thread(self):
        """Thread that handles sequential text output"""
        logger.info("Output manager thread started", extra=FILE_ONLY)

        while self.running:
            try:
                with self.output_condition:
                    # Wait until something needs to be done
                    while self.running:
                        # Check if next sequence is ready (positive sequence numbers)
                        if self.next_sequence_to_output in self.output_queue:
                            task = self.output_queue[self.next_sequence_to_output]
                            if task.is_complete:
                                # This sequence is ready!
                                del self.output_queue[self.next_sequence_to_output]
                                break

                        # Check for immediate tasks (negative sequence numbers)
                        immediate_tasks = [t for t in self.output_queue.values()
                                         if t.sequence_number < 0 and t.is_complete]
                        if immediate_tasks:
                            # Immediate tasks can always be output immediately
                            # Sort by sequence number (most recent first for negative numbers)
                            immediate_tasks.sort(key=lambda t: t.sequence_number, reverse=True)
                            task = immediate_tasks[0]
                            del self.output_queue[task.sequence_number]
                            break

                        # Nothing to do - wait with timeout
                        self.output_condition.wait(timeout=OUTPUT_QUEUE_TIMEOUT)

                        # Check if we should exit after timeout
                        if not self.running:
                            return
                    else:
                        # running is False
                        return

                    # Process task
                    if task.sequence_number < 0:  # Immediate task with negative sequence
                        logger.debug(f"Output manager: Processing immediate task (Seq: {task.sequence_number})")
                    else:
                        logger.debug(f"Output manager: Processing sequence {task.sequence_number}")
                        self.next_sequence_to_output += 1

                # Wait for key release if needed (for immediate inserts)
                if task.wait_for_key_release and task.trigger_keys:
                    logger.debug(f"Task {task.sequence_number} waiting for key release: {task.trigger_keys}")
                    max_wait = 2.0
                    start_time = time.time()

                    while time.time() - start_time < max_wait:
                        keys_pressed = False
                        for key in task.trigger_keys:
                            if is_key_pressed(key):
                                keys_pressed = True
                                break

                        if not keys_pressed:
                            logger.debug(f"All keys released for task {task.sequence_number}")
                            break

                        time.sleep(0.01)
                    else:
                        logger.warning(f"Timeout waiting for key release for task {task.sequence_number}")

                # Output text (outside of lock!)
                if task.transcript and not task.is_error:
                    # Check if auto-insert is disabled (text only saved for later)
                    if not task.auto_insert:
                        logger.debug(f"Text for sequence {task.sequence_number} processed but NOT inserted (auto_insert=False)")
                        # Surface the READY state on the console (#37)
                        if self.on_task_complete:
                            self.on_task_complete(event='ready',
                                                  seq=task.sequence_number,
                                                  chars=len(task.transcript))
                        # Continue to next task (don't insert)
                        continue

                    try:
                        if task.use_clipboard:
                            # Clipboard insertion
                            success = self._insert_text_via_clipboard(task.transcript)
                            if success:
                                # Log insertion
                                if task.sequence_number < 0:
                                    logger.debug(f"Immediate text inserted via clipboard (Seq: {task.sequence_number}, {len(task.transcript)} chars)")
                                else:
                                    logger.debug(f"Text for sequence {task.sequence_number} inserted via clipboard")

                                # Press Enter if send_after_insert is enabled
                                if task.send_after_insert:
                                    logger.debug(f"Pressing Enter to send message (Seq: {task.sequence_number})")
                                    time.sleep(0.2)  # Short pause for safety
                                    pyautogui.press('return')
                                    logger.debug(f"Text sent via Enter key (Seq: {task.sequence_number})")

                                # Call completion callback if registered
                                if self.on_task_complete:
                                    self.on_task_complete(event='inserted',
                                                          seq=task.sequence_number,
                                                          chars=len(task.transcript),
                                                          sent=task.send_after_insert)
                            else:
                                logger.error("Clipboard insertion failed")
                                if self.on_task_complete:
                                    self.on_task_complete(event='failed',
                                                          kind='insertion',
                                                          seq=task.sequence_number)
                        else:
                            # Keyboard typing using keyboard.write()
                            logger.debug("Typing text into active application using keyboard.write()")

                            # Wait until user has released all modifier keys
                            if not self._ensure_no_modifiers_pressed():
                                logger.error(f"Cannot insert sequence {task.sequence_number} - modifiers still pressed after timeout")
                                # Same root cause surfaces as a failed insert on
                                # the clipboard path (False return above), so
                                # report it here too (#37)
                                if self.on_task_complete:
                                    self.on_task_complete(event='failed',
                                                          kind='insertion',
                                                          seq=task.sequence_number)
                                continue

                            with self.keyboard_lock:
                                keyboard.write(task.transcript)

                            # Log insertion
                            if task.sequence_number < 0:
                                logger.debug(f"Immediate text inserted (Seq: {task.sequence_number}, {len(task.transcript)} chars)")
                            else:
                                logger.debug(f"Text for sequence {task.sequence_number} inserted")

                            # Press Enter if send_after_insert is enabled
                            if task.send_after_insert:
                                logger.debug(f"Pressing Enter to send message (Seq: {task.sequence_number})")
                                time.sleep(0.2)  # Short pause for safety
                                pyautogui.press('return')
                                logger.debug(f"Text sent via Enter key (Seq: {task.sequence_number})")

                            # Call completion callback if registered
                            if self.on_task_complete:
                                self.on_task_complete(event='inserted',
                                                      seq=task.sequence_number,
                                                      chars=len(task.transcript),
                                                      sent=task.send_after_insert)

                    except Exception as e:
                        logger.error(f"Error during insertion: {e}")
                        if self.on_task_complete:
                            self.on_task_complete(event='failed',
                                                  kind='insertion',
                                                  seq=task.sequence_number)
                elif task.no_speech:
                    # #133: every engine ran clean and returned empty -- the
                    # recording held no speech. A benign verdict, not a failure:
                    # route the honest 'no speech' panel, never the FAILED path.
                    logger.info(f"No speech in sequence {task.sequence_number}", extra=FILE_ONLY)
                    if self.on_task_complete:
                        self.on_task_complete(event='no_speech',
                                              seq=task.sequence_number)
                elif task.is_error:
                    logger.info(f"Skipping errored sequence {task.sequence_number}", extra=FILE_ONLY)
                    if self.on_task_complete:
                        self.on_task_complete(event='failed',
                                              kind='transcription',
                                              seq=task.sequence_number,
                                              reason=task.error_reason,
                                              provider=task.error_provider,
                                              inconclusive=task.error_inconclusive)

            except Exception as e:
                logger.error(f"Error in output manager: {e}", exc_info=True)

        logger.info("Output manager thread stopped", extra=FILE_ONLY)

    def stop(self):
        """Stop the output manager"""
        logger.info("Stopping output manager...", extra=FILE_ONLY)
        self.running = False
        with self.output_condition:
            self.output_condition.notify()

        # Wait for thread to finish
        self.output_thread.join(timeout=2)
        logger.info("Output manager stopped", extra=FILE_ONLY)
