"""Single-instance guard + focus-existing remedy for the settings app (#196, D-009).

The graphical settings app enforces one window: a second launch -- by Ctrl+Alt+G,
a double-click of Thoughtborne-Settings.bat, or the installer hand-off -- brings the
existing window to the front instead of stacking another editor of the same two
config files (D-002). This is the settings-app counterpart of the tool's own
single-instance mutex (D-004), with two deliberate differences: a *distinct* mutex
name (sharing the tool's would make a running tool block every settings launch and
vice versa) and a GUI remedy (focus, don't refuse -- bringing the window forward IS
the feedback, a notice would be noise). The remedy is a cross-process topmost pulse
that raises the window's Z-order without needing foreground rights (the reliable lift
where a background SetForegroundWindow is refused, #203), and it reports its outcome
as a FOCUS_* category the caller logs -- so a silent no-op is no longer
indistinguishable from a real raise.

The module also hosts `close_existing_settings_windows` (#222, D-014): the tool posts
WM_CLOSE to the same D-009 title-matched window on its own quit, so "one program, one
exit" holds and close can never drift from the focus match. It shares this module's
lazy-ctypes, fail-open shape.

Pure stdlib, so the D-005 system-Python rescue lane keeps working: the mutex/focus
is `ctypes`, imported lazily *inside* the Windows functions so `import
settings_instance` never fails off-Windows. The title helper is pure and imports the
DE/EN string table, so the four localized titles it matches can't drift from what the
window actually sets. Every Windows path is fail-open: any failure resolves to "start
normally" (no mutex held, no focus), so a guard fault can never cost a launch.

The mutex mechanics -- permissive security descriptor, session-scoped name,
ACCESS_DENIED counted as "already running", the handle held for the whole process --
are copied from `thoughtborne._second_instance_running`; see D-004 for the reasoning.
"""

import settings_strings as strings

# Distinct from the tool's "Thoughtborne-SingleInstance" (D-004): a shared name would
# make the running tool and the settings app mutually exclusive. Session-scoped (no
# Global\ prefix), matching the tool's mutex and the session scope of the desktop.
SETTINGS_MUTEX_NAME = "Thoughtborne-Settings-SingleInstance"

# Held for the whole process so the kernel frees it on any exit (D-004); never closed.
_MUTEX_HANDLE = None

# Outcome categories of focus_existing_settings_window (#203, D-009): a second launch
# logs which of these it achieved, so the log can tell a real raise from a silent
# no-op. Distinct string values -- test-guarded.
FOCUS_NOT_FOUND = "not-found"   # no matching window (also off-Windows / on exception)
FOCUS_RAISED = "raised"         # topmost pulse lifted it to the top, focus not taken
FOCUS_FOCUSED = "focused"       # it is now the OS foreground window (confirmed)
FOCUS_REFUSED = "refused"       # a window was found but even the pulse did not take


def settings_window_titles() -> tuple:
    """The exact set of titles a settings window can carry -- both modes
    (settings / first-run) x both languages (DE / EN) -- computed from the string
    table so the focus match can never drift from what the window sets. Pure and
    off-Windows testable."""
    keys = ("app.title.settings", "app.title.firstrun")
    titles = {strings.t(k, lang) for k in keys for lang in ("de", "en")}
    return tuple(sorted(titles))


def create_instance_mutex() -> tuple:
    """Create-or-open the settings single-instance mutex (D-004 mechanics).

    Returns (handle, already_running). `already_running` is True when another
    settings instance already holds the name -- an ERROR_ALREADY_EXISTS on our own
    create, or an ERROR_ACCESS_DENIED when a higher-integrity instance's permissive
    descriptor still denies our open (the elevated/normal pair). The handle is held
    module-global for the whole process; the caller may ignore it. Fail-open in every
    uncertain case -- off-Windows, a NULL handle for any other reason, any exception
    -> (None, False), so the app starts normally and the guard never costs a launch.
    """
    import os
    if os.name != "nt":
        return (None, False)
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        ERROR_ALREADY_EXISTS = 183
        ERROR_ACCESS_DENIED = 5

        class SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD),
                        ("lpSecurityDescriptor", wintypes.LPVOID),
                        ("bInheritHandle", wintypes.BOOL)]

        # Permissive descriptor so a medium-integrity instance can open a mutex an
        # elevated one created: DACL grants Everyone (WD) generic-all, a Low
        # mandatory label (LW, no-write-up) drops the integrity barrier. The
        # ACCESS_DENIED handling below is the suspenders to this belt (D-004).
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.ULONG)]
        psd = wintypes.LPVOID()
        sddl = "D:(A;;GA;;;WD)S:(ML;;NW;;;LW)"
        sd_ok = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(psd), None)   # 1 = SDDL_REVISION_1

        sa = SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
        sa.lpSecurityDescriptor = psd.value if sd_ok else None
        sa.bInheritHandle = False

        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            ctypes.POINTER(SECURITY_ATTRIBUTES), wintypes.BOOL, wintypes.LPCWSTR]
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(ctypes.byref(sa), False, SETTINGS_MUTEX_NAME)
        err = ctypes.get_last_error()

        if sd_ok and psd:
            kernel32.LocalFree.argtypes = [wintypes.LPVOID]
            kernel32.LocalFree.restype = wintypes.LPVOID
            kernel32.LocalFree(psd)     # the SD was copied into the kernel object

        if not handle:
            # Could not even open the name -- on the same session almost certainly
            # ACCESS_DENIED from a higher-integrity owner, i.e. already running.
            if err == ERROR_ACCESS_DENIED:
                return (None, True)
            return (None, False)        # fail-open on any other create failure

        global _MUTEX_HANDLE
        _MUTEX_HANDLE = handle          # keep for the whole process (kernel frees it)
        return (handle, err in (ERROR_ALREADY_EXISTS, ERROR_ACCESS_DENIED))
    except Exception:
        return (None, False)            # fail-open, always


def focus_existing_settings_window() -> str:
    """Bring an already-open settings window to the front and report the outcome as
    one of the FOCUS_* categories (#203, D-009). Enumerates top-level windows, matches
    the title *exactly* against the four known localized titles (an unmapped-yet window
    in a near-simultaneous start simply isn't found -- the "at most one window"
    guarantee still holds via the mutex), restores it if minimized, raises it with a
    cross-process topmost pulse, then tries to hand it real keyboard focus.

    The topmost pulse (SetWindowPos to HWND_TOPMOST then back to HWND_NOTOPMOST, with
    NOACTIVATE) is the load-bearing remedy: a background process may reorder another
    window's Z-order without holding foreground rights, so this reliably lifts the
    window into view where a plain SetForegroundWindow is refused (the #199-observed
    ineffective AttachThreadInput path). NOACTIVATE means the pulse steals no keyboard
    focus; the subsequent SetForegroundWindow (+ AttachThreadInput fallback) attempts
    real focus on top, and a GetForegroundWindow re-probe distinguishes the outcome.

    Returns:
      FOCUS_FOCUSED    the window is now the OS foreground window (re-probe confirmed),
      FOCUS_RAISED     the pulse lifted it to the top but focus was refused,
      FOCUS_REFUSED    a window was found but even the pulse did not take,
      FOCUS_NOT_FOUND  no matching window -- also off-Windows or on any exception (fail-open).
    """
    import os
    if os.name != "nt":
        return FOCUS_NOT_FOUND
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        wanted = set(settings_window_titles())
        found = {"hwnd": None}

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if buf.value in wanted:
                    found["hwnd"] = hwnd
                    return False    # stop enumerating
            except Exception:
                pass
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        hwnd = found["hwnd"]
        if not hwnd:
            return FOCUS_NOT_FOUND

        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)

        # Topmost pulse: raise the window's Z-order to the very top and immediately
        # drop the topmost flag again, without activating it (SWP_NOACTIVATE). A
        # background process is allowed this cross-process reorder even when it holds
        # no foreground rights, so this is the reliable "visibly on top" remedy.
        HWND_TOPMOST = wintypes.HWND(-1)
        HWND_NOTOPMOST = wintypes.HWND(-2)
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        swp = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        raised = bool(user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, swp))
        if raised:
            # Always drop the topmost flag again, retrying once and in its own
            # try/except so a failure here can't skip the focus attempt below. A
            # TOPMOST left in place (its NOTOPMOST partner failing) would pin the
            # window permanently above everything -- rare (the opposite-flag call on
            # the same window) and not fatal (it stays visibly in front, self-clearing
            # on the next normal activation), so `raised` stays the honest verdict.
            try:
                if not user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, swp):
                    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, swp)
            except Exception:
                pass

        user32.BringWindowToTop(hwnd)
        if not user32.SetForegroundWindow(hwnd):
            # Foreground refused (we are a background process): attach our input thread
            # to the current foreground window's thread so Windows lets us hand focus
            # over, then detach again. Best-effort -- a cross-integrity UIPI block still
            # leaves the window raised by the pulse above.
            our_tid = kernel32.GetCurrentThreadId()
            fg = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(fg_tid) and fg_tid != our_tid and \
                bool(user32.AttachThreadInput(our_tid, fg_tid, True))
            try:
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(our_tid, fg_tid, False)

        # Re-probe: did we actually become the foreground window? This is what makes
        # focused / raised / refused distinguishable in the log.
        focused = False
        try:
            focused = user32.GetForegroundWindow() == hwnd
        except Exception:
            focused = False
        if focused:
            return FOCUS_FOCUSED
        return FOCUS_RAISED if raised else FOCUS_REFUSED
    except Exception:
        return FOCUS_NOT_FOUND


def close_existing_settings_windows() -> int:
    """Post WM_CLOSE to every open settings window; return how many were asked to
    close (#222, D-014). The tool calls this on its own quit so "one program, one
    exit" holds -- the settings window ends with the tool. Best-effort and
    NON-BLOCKING: PostMessageW queues the close in the settings process and returns
    at once (never SendMessageW, which would block on the target's message loop and
    could hang the quit on a wedged settings process). Since #221/D-014 the window's
    WM_DELETE_WINDOW handler is just root.destroy, so WM_CLOSE closes it cleanly with
    no prompt.

    Enumerates top-level windows and matches the title EXACTLY against the four known
    localized settings/first-run titles (the same D-009 set focus_existing_settings_window
    uses, so close and focus can never drift), which keeps the tool's own console
    window out of the match. Closes ALL matches though the mutex allows only one.
    Returns the count of windows posted to -- 0 when none are open (the
    settings-never-opened / already-closed / crashed cases the spec's "best-effort
    close finds nothing" names). Fail-open: off-Windows or on any exception -> 0, so a
    guard fault can never cost the quit. WM_CLOSE = 0x0010.
    """
    import os
    if os.name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL

        wanted = set(settings_window_titles())
        found = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if buf.value in wanted:
                    found.append(hwnd)
            except Exception:
                pass
            return True     # keep enumerating -- close ALL matches, not just the first

        user32.EnumWindows(WNDENUMPROC(_cb), 0)

        WM_CLOSE = 0x0010
        posted = 0
        for hwnd in found:
            try:
                if user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
                    posted += 1
            except Exception:
                pass
        return posted
    except Exception:
        return 0            # fail-open, always
