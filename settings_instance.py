"""Single-instance guard + focus-existing remedy for the settings app (#196, D-009).

The graphical settings app enforces one window: a second launch -- by Ctrl+Alt+G,
a double-click of Thoughtborne-Settings.bat, or the installer hand-off -- brings the
existing window to the front instead of stacking another editor of the same two
config files (D-002). This is the settings-app counterpart of the tool's own
single-instance mutex (D-004), with two deliberate differences: a *distinct* mutex
name (sharing the tool's would make a running tool block every settings launch and
vice versa) and a GUI remedy (focus, don't refuse -- bringing the window forward IS
the feedback, a notice would be noise).

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


def focus_existing_settings_window() -> bool:
    """Bring an already-open settings window to the front. Enumerates top-level
    windows, matches the title *exactly* against the four known localized titles
    (an unmapped-yet window in a near-simultaneous start simply isn't found -- the
    "at most one window" guarantee still holds via the mutex), restores it if
    minimized, and raises it with the documented AttachThreadInput fallback for when
    a background process's plain SetForegroundWindow is refused. Returns True when a
    window was found and addressed. Fail-open: off-Windows or any exception -> False.
    """
    import os
    if os.name != "nt":
        return False
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
            return False

        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        if user32.SetForegroundWindow(hwnd):
            return True

        # Foreground refused (we are a background process): attach our input thread
        # to the current foreground window's thread so Windows lets us hand focus
        # over, then detach again. Best-effort -- a cross-integrity UIPI block still
        # leaves the window raised; "at most one window" already holds.
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
        return True
    except Exception:
        return False
