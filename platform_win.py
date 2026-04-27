# Copyright (c) 2026 Myles Willett. All rights reserved.
# Proprietary and confidential. No reproduction, distribution, or use
# without express written permission.

"""
WillettBot Platform Backend — Windows
======================================
Implements the platform_helpers public API on Windows using:
  - pywin32              (win32gui, win32process, win32con, win32com.client)
  - Shell.Application    (COM — to read Explorer's current folder + selection)
  - subprocess + cmd     (start, taskkill, etc.)
  - PowerShell           (for the native_script action)

DEPENDENCIES (will be installed by the bundled-python bootstrapper on Windows):
    pywin32     — pip install pywin32
    pyautogui   — already present, works on Windows
    pynput      — already present, works on Windows

Permission model: Windows has no TCC-equivalent gate for input simulation.
pyautogui clicks and pynput listeners just work (UAC only matters if you're
trying to control an elevated app from a non-elevated one — out of scope for
WillettBot's current use cases). All check_*() functions return True.

UNTESTED — written from spec / pywin32 docs. To test, boot into Bootcamp,
clone the repo, install pywin32, and run `python recorder.py --start-hotkey
f9 --end-hotkey f10 --name test`.
"""

import os
import sys
import time
import shutil
import subprocess


# ── PLATFORM CONSTANTS ──────────────────────────────────────────────────────
PLATFORM_NAME             = 'Windows'
NATIVE_SCRIPT_ACTION      = 'powershell'
NEEDS_ACCESSIBILITY_GRANT = False   # No TCC equivalent on Windows


# ── pywin32 lazy import ─────────────────────────────────────────────────────
# pywin32 is only available on Windows. We import lazily so this file can at
# least be syntax-checked / imported on macOS during cross-platform CI.

_w32_modules = None
_w32_import_error = None


def _w32():
    """Returns the pywin32 module bundle, or None if pywin32 isn't installed.
    Stores the import error for diagnostics."""
    global _w32_modules, _w32_import_error
    if _w32_modules is not None:
        return _w32_modules
    if _w32_import_error is not None:
        return None
    try:
        import win32gui          # noqa
        import win32process      # noqa
        import win32con          # noqa
        import win32api          # noqa
        import win32com.client   # noqa
        _w32_modules = {
            'gui': win32gui,
            'process': win32process,
            'con': win32con,
            'api': win32api,
            'com': win32com.client,
        }
        return _w32_modules
    except Exception as e:
        _w32_import_error = str(e)
        return None


def _process_name_from_hwnd(hwnd):
    """Map a window handle → process name (e.g. 'chrome.exe'). Returns ''
    if pywin32 isn't available or the lookup failed."""
    w = _w32()
    if not w or not hwnd:
        return ''
    try:
        _, pid = w['process'].GetWindowThreadProcessId(hwnd)
        # Open process with QUERY_LIMITED_INFORMATION (0x1000) — works without
        # admin even for protected apps. Then GetProcessImageFileName.
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = w['api'].OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            full = w['process'].GetModuleFileNameEx(h, 0)
        finally:
            w['api'].CloseHandle(h)
        return os.path.basename(full)  # e.g. 'chrome.exe'
    except Exception:
        return ''


def _friendly_app_name(exe):
    """'chrome.exe' → 'Chrome'. Map known executables; fall back to Title-Cased
    basename without the .exe. Used to keep recorded scripts portable across
    machines where the same logical app might live at different paths."""
    if not exe:
        return ''
    base = exe.lower().replace('.exe', '')
    known = {
        'explorer':       'Explorer',
        'chrome':         'Google Chrome',
        'msedge':         'Microsoft Edge',
        'firefox':        'Firefox',
        'iexplore':       'Internet Explorer',
        'outlook':        'Outlook',
        'winword':        'Word',
        'excel':          'Excel',
        'powerpnt':       'PowerPoint',
        'onenote':        'OneNote',
        'teams':          'Microsoft Teams',
        'slack':          'Slack',
        'discord':        'Discord',
        'spotify':        'Spotify',
        'code':           'Visual Studio Code',
        'notepad':        'Notepad',
        'wordpad':        'WordPad',
        'mspaint':        'Paint',
        'calc':           'Calculator',
        'cmd':            'Command Prompt',
        'powershell':     'PowerShell',
        'pwsh':           'PowerShell',
        'wt':             'Windows Terminal',
        'photos':         'Photos',
        'mstsc':          'Remote Desktop',
    }
    if base in known:
        return known[base]
    return base.capitalize()


# ── QUERIES ─────────────────────────────────────────────────────────────────

def get_frontmost_app(timeout=2.0):
    """Friendly name of the foreground app, or '' on failure. Maps the .exe
    of the foreground window's process to a human-readable name (Explorer,
    Chrome, Word, etc.)."""
    w = _w32()
    if not w:
        return ''
    try:
        hwnd = w['gui'].GetForegroundWindow()
        if not hwnd:
            return ''
        exe = _process_name_from_hwnd(hwnd)
        return _friendly_app_name(exe)
    except Exception:
        return ''


def get_frontmost_window_title(timeout=2.0):
    """Title of the foreground window, or ''."""
    w = _w32()
    if not w:
        return ''
    try:
        hwnd = w['gui'].GetForegroundWindow()
        if not hwnd:
            return ''
        return w['gui'].GetWindowText(hwnd) or ''
    except Exception:
        return ''


def get_window_count(app_name, timeout=2.0):
    """Number of top-level visible windows whose process maps to `app_name`,
    or None if pywin32 isn't available."""
    w = _w32()
    if not w or not app_name:
        return None
    target_friendly = app_name
    count = [0]

    def _enum(hwnd, _):
        try:
            if not w['gui'].IsWindowVisible(hwnd):
                return True
            # Skip windows with no title — toolwindows / hidden helpers.
            if not w['gui'].GetWindowText(hwnd):
                return True
            exe = _process_name_from_hwnd(hwnd)
            if _friendly_app_name(exe) == target_friendly:
                count[0] += 1
        except Exception:
            pass
        return True

    try:
        w['gui'].EnumWindows(_enum, None)
        return count[0]
    except Exception:
        return None


def get_file_manager_name():
    """Windows file manager is Explorer."""
    return 'Explorer'


def _shell_explorer_windows():
    """Iterate Shell.Application's open folder windows. Yields each COM
    InternetExplorer/ShellWindow object whose document is a folder. Empty
    iterator on COM failure."""
    w = _w32()
    if not w:
        return
    try:
        shell = w['com'].Dispatch('Shell.Application')
        for win in shell.Windows():
            try:
                # Filter to actual folder windows; skip browser windows that
                # also live in Shell.Windows().
                doc = win.Document
                if doc is None:
                    continue
                # Folder windows expose .Folder.Self.Path; browser windows don't.
                if not hasattr(doc, 'Folder'):
                    continue
                yield win
            except Exception:
                continue
    except Exception:
        return


def get_file_manager_front_path(timeout=1.5):
    """Path of the FOREGROUND Explorer window, or '' if Explorer isn't front
    or has no folder open."""
    w = _w32()
    if not w:
        return ''
    try:
        front_hwnd = w['gui'].GetForegroundWindow()
        for win in _shell_explorer_windows():
            try:
                if int(win.HWND) == int(front_hwnd):
                    return win.Document.Folder.Self.Path
            except Exception:
                continue
    except Exception:
        pass
    return ''


def get_file_manager_selection(timeout=1.5):
    """Path of the first selected item in the foreground Explorer window."""
    w = _w32()
    if not w:
        return ''
    try:
        front_hwnd = w['gui'].GetForegroundWindow()
        for win in _shell_explorer_windows():
            try:
                if int(win.HWND) != int(front_hwnd):
                    continue
                items = win.Document.SelectedItems()
                if items.Count == 0:
                    return ''
                first = items.Item(0)
                return first.Path
            except Exception:
                continue
    except Exception:
        pass
    return ''


# ── APP / WINDOW CONTROL ────────────────────────────────────────────────────

def _set_foreground_window(hwnd):
    """Bring `hwnd` to the foreground. Windows fights cross-process focus
    swaps for security; the AttachThreadInput dance is the standard
    workaround. Returns True on apparent success."""
    w = _w32()
    if not w or not hwnd:
        return False
    try:
        gui = w['gui']
        # Restore if minimized.
        if gui.IsIconic(hwnd):
            gui.ShowWindow(hwnd, w['con'].SW_RESTORE)
        # AttachThreadInput trick: temporarily attach our input queue to the
        # target window's thread so SetForegroundWindow is allowed.
        fg = gui.GetForegroundWindow()
        cur_thread = w['api'].GetCurrentThreadId()
        target_thread, _ = w['process'].GetWindowThreadProcessId(hwnd)
        attached = False
        if target_thread and target_thread != cur_thread:
            try:
                w['process'].AttachThreadInput(cur_thread, target_thread, True)
                attached = True
            except Exception:
                pass
        try:
            gui.BringWindowToTop(hwnd)
            gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                try:
                    w['process'].AttachThreadInput(cur_thread, target_thread, False)
                except Exception:
                    pass
        return gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def activate_app(app_name, timeout=2.0):
    """Bring any window of `app_name` to the foreground. Returns True if a
    matching window was found and the foreground swap appears to have taken."""
    w = _w32()
    if not w or not app_name:
        return False
    target_hwnd = [0]

    def _enum(hwnd, _):
        try:
            if not w['gui'].IsWindowVisible(hwnd):
                return True
            if not w['gui'].GetWindowText(hwnd):
                return True
            exe = _process_name_from_hwnd(hwnd)
            if _friendly_app_name(exe) == app_name:
                target_hwnd[0] = hwnd
                return False  # stop enumeration
        except Exception:
            pass
        return True

    try:
        w['gui'].EnumWindows(_enum, None)
    except Exception:
        pass
    if not target_hwnd[0]:
        return False
    return _set_foreground_window(target_hwnd[0])


def raise_window_by_title(app_name, title, timeout=2.5):
    """Find the window of `app_name` whose title matches `title` (full or
    24-char prefix) and bring it to the foreground."""
    w = _w32()
    if not w or not title:
        return False
    title_full = title
    title_prefix = title[:24] if len(title) > 24 else title
    target_hwnd = [0]

    def _enum(hwnd, _):
        try:
            if not w['gui'].IsWindowVisible(hwnd):
                return True
            wtitle = w['gui'].GetWindowText(hwnd)
            if not wtitle:
                return True
            if app_name:
                exe = _process_name_from_hwnd(hwnd)
                if _friendly_app_name(exe) != app_name:
                    return True
            if wtitle == title_full or wtitle.startswith(title_prefix):
                target_hwnd[0] = hwnd
                return False
        except Exception:
            pass
        return True

    try:
        w['gui'].EnumWindows(_enum, None)
    except Exception:
        pass
    if not target_hwnd[0]:
        return False
    return _set_foreground_window(target_hwnd[0])


def focus_modal_dialog(app_name, timeout=2.5):
    """Windows dialogs are separate top-level windows, not 'sheets attached
    to a parent'. Find any visible owned popup window for the target app and
    raise it. Returns True iff one was found and raised.

    On Mac this exists to un-dim modal sheets; on Windows the equivalent is
    re-foregrounding a child dialog when something else stole focus. If the
    target app has only one visible window, this is a cheap no-op."""
    w = _w32()
    if not w or not app_name:
        return False
    candidate = [0]

    def _enum(hwnd, _):
        try:
            if not w['gui'].IsWindowVisible(hwnd):
                return True
            if not w['gui'].GetWindowText(hwnd):
                return True
            # Owned popup = has an owner window and WS_POPUP style. A dialog
            # box matches; a regular top-level window doesn't.
            owner = w['gui'].GetWindow(hwnd, w['con'].GW_OWNER)
            if not owner:
                return True
            exe = _process_name_from_hwnd(hwnd)
            if _friendly_app_name(exe) != app_name:
                return True
            candidate[0] = hwnd
            return False
        except Exception:
            pass
        return True

    try:
        w['gui'].EnumWindows(_enum, None)
    except Exception:
        return False
    if not candidate[0]:
        return False
    return _set_foreground_window(candidate[0])


def wait_until_frontmost(expected, timeout=1.2):
    """Poll until `expected` is the frontmost app, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if get_frontmost_app() == expected:
            return True
        time.sleep(0.05)
    return False


def open_app(name, timeout=5.0):
    """Launch or focus an app by name. Two strategies:
      1. Try to activate an existing window first (cheap + reliable).
      2. If none, shell out to `start "" "<name>"` which uses Windows'
         App Path lookup — works for installed apps registered in the
         AppPaths registry (Chrome, Word, Excel, etc.)."""
    if not name:
        return
    if activate_app(name):
        return
    # Strip our friendly name back to a likely .exe (e.g. 'Chrome' →
    # 'chrome'). `start` will append .exe / search PATH / consult AppPaths.
    candidate = name
    # If the friendly map round-trips (e.g. 'Google Chrome' → 'chrome.exe'),
    # use that. Otherwise pass the user's literal text and let `start` figure
    # it out via AppPaths.
    reverse = {
        'Google Chrome': 'chrome',
        'Microsoft Edge': 'msedge',
        'Firefox': 'firefox',
        'Internet Explorer': 'iexplore',
        'Outlook': 'outlook',
        'Word': 'winword',
        'Excel': 'excel',
        'PowerPoint': 'powerpnt',
        'OneNote': 'onenote',
        'Microsoft Teams': 'teams',
        'Slack': 'slack',
        'Discord': 'discord',
        'Spotify': 'spotify',
        'Visual Studio Code': 'code',
        'Notepad': 'notepad',
        'WordPad': 'wordpad',
        'Paint': 'mspaint',
        'Calculator': 'calc',
        'Command Prompt': 'cmd',
        'PowerShell': 'powershell',
        'Windows Terminal': 'wt',
        'Photos': 'ms-photos:',  # URI scheme — modern UWP app
        'Remote Desktop': 'mstsc',
        'Explorer': 'explorer',
    }
    candidate = reverse.get(name, name)
    try:
        # `start "" <thing>` — empty title arg is required when the first
        # arg has spaces or quotes; shell=True so cmd.exe handles it.
        subprocess.Popen(
            ['cmd', '/c', 'start', '', candidate],
            shell=False, timeout=None
        )
    except Exception:
        pass


def open_file(path, app='', timeout=10.0):
    """Open `path` with the default app, or with `app` if provided. Returns
    (ok, error_message). Handles URLs (http://, mailto:, file://) too."""
    if not path:
        return (False, 'open_file: empty path')
    try:
        is_url = '://' in path
        if app:
            # `start "" "AppName" "path"` — Windows resolves the app via
            # AppPaths, then passes the path as its first argument.
            reverse = {
                'Google Chrome': 'chrome',
                'Microsoft Edge': 'msedge',
                'Firefox': 'firefox',
                'Word': 'winword',
                'Excel': 'excel',
                'PowerPoint': 'powerpnt',
                'Notepad': 'notepad',
                'Visual Studio Code': 'code',
            }
            exe = reverse.get(app, app)
            r = subprocess.run(
                ['cmd', '/c', 'start', '', exe, path],
                capture_output=True, text=True, timeout=timeout
            )
            if r.returncode != 0:
                err = (r.stderr or '').strip() or 'start exit ' + str(r.returncode)
                return (False, err)
            return (True, '')
        # No specific app — os.startfile() handles BOTH files and URLs on
        # Windows. CRITICAL: do NOT route URLs through `cmd /c start ""` —
        # cmd.exe treats `&` as a command separator and chops Gmail-style
        # URLs (?view=cm&fs=1&tf=1&to=...) at the first `&`, opening only
        # the URL prefix. os.startfile() goes straight to ShellExecute and
        # never sees cmd.exe's parser, so the full URL is preserved.
        try:
            os.startfile(path)
            return (True, '')
        except OSError as e:
            return (False, str(e))
    except subprocess.TimeoutExpired:
        return (False, 'start timed out after ' + str(timeout) + 's')
    except Exception as e:
        return (False, str(e))


def open_directory_in_place(path, timeout=5.0):
    """Retarget the foreground Explorer window to `path` instead of spawning
    a new one. Uses the Shell.Application COM 'Navigate2' on the foreground
    window. Falls back to False if no Explorer window is foreground (caller
    should fall back to os.startfile)."""
    w = _w32()
    if not w or not path:
        return False
    try:
        front_hwnd = w['gui'].GetForegroundWindow()
        for win in _shell_explorer_windows():
            try:
                if int(win.HWND) != int(front_hwnd):
                    continue
                # Navigate2(URL) — accepts a filesystem path on Windows.
                win.Navigate2(path)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# ── PERMISSION PROBES ───────────────────────────────────────────────────────

def check_accessibility():
    """Windows has no TCC equivalent for input simulation. pyautogui clicks
    just work. Return True so the upstream UI doesn't show a "fix permissions"
    banner that doesn't apply."""
    return True


def check_automation():
    """No equivalent permission gate on Windows."""
    return True


def probe_automation_permission():
    """Recorder-startup probe — on Windows we just confirm pywin32 imports.
    If it doesn't, context detection (frontmost app, Explorer selection,
    etc.) won't work but plain click/keystroke recording still will."""
    if _w32():
        return {
            'status': 'ok', 'returncode': 0, 'stdout': '', 'stderr': '',
            'osascript': True   # field name kept for protocol compat; means
                                # "the platform's automation backend is alive"
        }
    return {
        'status': 'error', 'returncode': None,
        'stdout': '',
        'stderr': 'pywin32 import failed: ' + (_w32_import_error or 'unknown'),
        'osascript': False
    }


# ── NATIVE SCRIPTING (`powershell` action) ──────────────────────────────────

def run_native_script(script, timeout=30.0):
    """Run a PowerShell script and return stdout. Raises TimeoutError on
    timeout, RuntimeError on non-zero exit."""
    if isinstance(script, list):
        script = '\n'.join(str(x) for x in script)
    # -NoProfile keeps startup fast; -Command runs the script literal.
    # We pass via stdin to avoid quoting hell on the command line.
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-Command', '-'],
            input=script,
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError('PowerShell timed out after ' + str(timeout) + 's')
    if r.returncode != 0:
        err = (r.stderr or '').strip() or 'powershell exit ' + str(r.returncode)
        raise RuntimeError('PowerShell failed: ' + err)
    return (r.stdout or '').strip()
