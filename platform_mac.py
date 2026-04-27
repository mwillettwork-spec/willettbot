# Copyright (c) 2026 Myles Willett. All rights reserved.
# Proprietary and confidential. No reproduction, distribution, or use
# without express written permission.

"""
WillettBot Platform Backend — macOS
====================================
Implements the platform_helpers public API using:
  - osascript / AppleScript     (frontmost app, Finder selection, AXRaise)
  - AXIsProcessTrusted          (Accessibility permission probe)
  - `open` / `open -a`          (launching files and apps)

Every public function below has a matching implementation in platform_win.py
and platform_linux.py so callers don't need to branch on sys.platform.
"""

import os
import sys
import time
import subprocess


# ── PLATFORM CONSTANTS ──────────────────────────────────────────────────────
PLATFORM_NAME             = 'macOS'
NATIVE_SCRIPT_ACTION      = 'applescript'   # the script-action name in JSON
NEEDS_ACCESSIBILITY_GRANT = True            # macOS gates input simulation via TCC


# ── INTERNAL: osascript shell ───────────────────────────────────────────────
# Pinning to /usr/bin/osascript (not relying on PATH) because the bundled
# Python environment Electron spawns inherits a minimal PATH that sometimes
# doesn't include /usr/bin on managed Macs.
_OSASCRIPT_BIN = '/usr/bin/osascript'


def _esc(s):
    """Escape a string for safe inclusion inside an AppleScript double-quoted
    literal. Backslash and double-quote are the only chars AppleScript treats
    specially inside "...". Backslash MUST be escaped first."""
    return s.replace('\\', '\\\\').replace('"', '\\"')


def _osascript(script_lines, timeout=2.0):
    """Run an AppleScript via /usr/bin/osascript.

    `script_lines` may be a single string or a list of lines (each passed as
    its own -e argument, which is how the recorder built them).

    Returns stdout stripped, or '' on any failure (timeout, non-zero exit,
    permission denial). Callers that need to distinguish failure modes should
    use _osascript_full() instead."""
    try:
        if isinstance(script_lines, str):
            args = [_OSASCRIPT_BIN, '-e', script_lines]
        else:
            args = [_OSASCRIPT_BIN]
            for line in script_lines:
                args += ['-e', line]
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return ''
        return (r.stdout or '').strip()
    except Exception:
        return ''


def _osascript_full(script_lines, timeout=2.0):
    """Same as _osascript but returns (returncode, stdout, stderr) so the
    caller can inspect failure details. Used for the permission-probe and the
    public run_native_script() path that needs to surface real errors."""
    try:
        if isinstance(script_lines, str):
            args = [_OSASCRIPT_BIN, '-e', script_lines]
        else:
            args = [_OSASCRIPT_BIN]
            for line in script_lines:
                args += ['-e', line]
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip())
    except subprocess.TimeoutExpired:
        return (-1, '', 'timeout')
    except Exception as e:
        return (-1, '', 'exception: ' + str(e))


# ── QUERIES (no side effects) ───────────────────────────────────────────────

def get_frontmost_app(timeout=2.0):
    """Name of the frontmost app, or '' on failure."""
    return _osascript([
        'tell application "System Events" to get name of first '
        'application process whose frontmost is true'
    ], timeout=timeout)


def get_frontmost_window_title(timeout=2.0):
    """Title of the frontmost window of the frontmost app, or ''."""
    return _osascript([
        'tell application "System Events"',
        'try',
        'set p to first application process whose frontmost is true',
        'if (count of windows of p) is 0 then return ""',
        'return name of front window of p',
        'on error',
        'return ""',
        'end try',
        'end tell'
    ], timeout=timeout)


def get_window_count(app_name, timeout=2.0):
    """Number of windows owned by `app_name`, or None if we can't tell."""
    if not app_name:
        return None
    safe = _esc(app_name)
    out = _osascript([
        'tell application "System Events"',
        'try',
        'if not (exists process "' + safe + '") then return "?"',
        'return (count of windows of process "' + safe + '") as string',
        'on error',
        'return "?"',
        'end try',
        'end tell'
    ], timeout=timeout)
    if not out or out == '?':
        return None
    try:
        return int(out)
    except ValueError:
        return None


def get_file_manager_name():
    """The platform's file-manager app name. Used by the recorder to gate
    Finder/Explorer-specific polling logic."""
    return 'Finder'


def get_file_manager_front_path(timeout=1.5):
    """POSIX path of the front Finder window's target folder, or ''."""
    return _osascript([
        'tell application "Finder"',
        'try',
        'if (count of windows) is 0 then return ""',
        'return POSIX path of (target of front window as alias)',
        'on error',
        'return ""',
        'end try',
        'end tell'
    ], timeout=timeout)


def get_file_manager_selection(timeout=1.5):
    """POSIX path of the first-selected Finder item, or ''."""
    return _osascript([
        'tell application "Finder"',
        'try',
        'set sel to selection',
        'if (count of sel) is 0 then return ""',
        'return POSIX path of ((item 1 of sel) as alias)',
        'on error',
        'return ""',
        'end try',
        'end tell'
    ], timeout=timeout)


# ── APP / WINDOW CONTROL ────────────────────────────────────────────────────

def activate_app(app_name, timeout=2.0):
    """Force `app_name` frontmost using System Events. Stronger than `open -a`
    when the app is already running but a modal sheet from another process
    grabbed focus."""
    if not app_name:
        return False
    rc, _, _ = _osascript_full(
        'tell application "' + _esc(app_name) + '" to activate',
        timeout=timeout
    )
    return rc == 0


def raise_window_by_title(app_name, title, timeout=2.5):
    """Bring the window with the matching title to the front of `app_name`.
    Matches by exact name OR by 24-char prefix so suffixes like '— Edited'
    don't break the anchor. Returns True iff a window matched and AXRaise
    succeeded."""
    if not app_name or not title:
        return False
    t_full = _esc(title)
    t_prefix = _esc(title[:24] if len(title) > 24 else title)
    script = '\n'.join([
        'tell application "System Events"',
        '  tell process "' + _esc(app_name) + '"',
        '    try',
        '      set theWindows to every window',
        '      repeat with w in theWindows',
        '        set wn to name of w',
        '        if wn is "' + t_full + '" or wn starts with "' + t_prefix + '" then',
        '          perform action "AXRaise" of w',
        '          set frontmost to true',
        '          return "ok"',
        '        end if',
        '      end repeat',
        '      return "nomatch"',
        '    on error errmsg',
        '      return "err:" & errmsg',
        '    end try',
        '  end tell',
        'end tell',
    ])
    rc, out, _ = _osascript_full(script, timeout=timeout)
    return rc == 0 and out == 'ok'


def focus_modal_dialog(app_name, timeout=2.5):
    """If `app_name` has a modal sheet attached to any window, raise that
    window so the sheet's buttons are clickable. macOS sheets (Save, file-copy
    'Replace / Keep Both', etc.) can't be AXRaised directly — they're always
    attached to a parent window — but if the parent isn't topmost the sheet
    appears dimmed and clicks may not register."""
    if not app_name:
        return False
    script = '\n'.join([
        'tell application "System Events"',
        '  tell process "' + _esc(app_name) + '"',
        '    try',
        '      set frontmost to true',
        '      repeat with w in (every window)',
        '        if (count of sheets of w) > 0 then',
        '          perform action "AXRaise" of w',
        '          return "raised"',
        '        end if',
        '      end repeat',
        '      return "none"',
        '    on error errmsg',
        '      return "err:" & errmsg',
        '    end try',
        '  end tell',
        'end tell',
    ])
    rc, out, _ = _osascript_full(script, timeout=timeout)
    return rc == 0 and out == 'raised'


def wait_until_frontmost(expected, timeout=1.2):
    """Poll until `expected` is the frontmost app, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if get_frontmost_app() == expected:
            return True
        time.sleep(0.05)
    return False


def open_app(name, timeout=5.0):
    """`open -a "AppName"` — launches the app OR brings it to the front if
    running. Silent on failure (caller can poll get_frontmost_app() to check)."""
    if not name:
        return
    try:
        subprocess.run(['open', '-a', name], check=False, timeout=timeout)
    except Exception:
        pass


def open_file(path, app='', timeout=10.0):
    """Open `path` with the default app, or with `app` if provided. URLs
    (http://, mailto:, file://) pass through. Returns (ok, error_message)."""
    if not path:
        return (False, 'open_file: empty path')
    cmd = ['open']
    if app:
        cmd += ['-a', app]
    cmd.append(path)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr or '').strip() or 'open exit ' + str(r.returncode)
            return (False, err)
        return (True, '')
    except subprocess.TimeoutExpired:
        return (False, 'open timed out after ' + str(timeout) + 's')
    except Exception as e:
        return (False, str(e))


def open_directory_in_place(path, timeout=5.0):
    """Retarget the front Finder window to `path` instead of spawning a new
    one. Returns True on success, False if no Finder window exists or
    AppleScript failed (caller should fall back to plain open_file)."""
    if not path:
        return False
    script = (
        'tell application "Finder"\n'
        '  activate\n'
        '  if (count of windows) is 0 then\n'
        '    make new Finder window to (POSIX file "' + _esc(path) + '" as alias)\n'
        '  else\n'
        '    set target of front window to (POSIX file "' + _esc(path) + '" as alias)\n'
        '  end if\n'
        'end tell'
    )
    rc, _, _ = _osascript_full(script, timeout=timeout)
    return rc == 0


# ── PERMISSION PROBES ───────────────────────────────────────────────────────

def check_accessibility():
    """AXIsProcessTrusted() — True iff this binary is allowed to post mouse /
    keyboard events. Returns None when the call itself fails (treated as
    'probably ok' upstream). Note: on unsigned binaries this can false-negative
    even when clicks actually work, so callers WARN rather than abort."""
    try:
        import ctypes, ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library('ApplicationServices'))
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        if bool(lib.AXIsProcessTrusted()):
            return True
        return None
    except Exception:
        return None


def check_automation():
    """Harmless osascript ping. False on -1743 / 'not authorized' errors,
    True on success, None on unrelated failures."""
    rc, out, stderr = _osascript_full(
        'tell application "System Events" to get name of first '
        'application process whose frontmost is true',
        timeout=3.0
    )
    if rc == 0 and out:
        return True
    stderr_low = stderr.lower()
    if ('-1743' in stderr_low
            or 'not authorized to send apple events' in stderr_low
            or 'not allowed assistive access' in stderr_low):
        return False
    return None


def probe_automation_permission():
    """Synchronous 10-second probe used at recorder startup. Returns a dict
    so the UI can show diagnostic output:
        {status, returncode, stdout, stderr, osascript}
    where status is one of: 'ok' | 'denied' | 'silent' | 'timeout' | 'error'."""
    out = {
        'status': 'error', 'returncode': None, 'stdout': '', 'stderr': '',
        'osascript': os.path.exists(_OSASCRIPT_BIN)
    }
    if not out['osascript']:
        out['stderr'] = _OSASCRIPT_BIN + ' not found'
        return out
    try:
        # Long timeout because the macOS Automation prompt BLOCKS osascript
        # until the user clicks Allow/Don't Allow. <6s risks timing out while
        # the user is still reading the dialog.
        r = subprocess.run(
            [_OSASCRIPT_BIN, '-e',
             'tell application "System Events" to get name of first '
             'application process whose frontmost is true'],
            capture_output=True, text=True, timeout=10
        )
        out['returncode'] = r.returncode
        out['stdout'] = (r.stdout or '').strip()
        out['stderr'] = (r.stderr or '').strip()
        if r.returncode == 0 and out['stdout']:
            out['status'] = 'ok'
            return out
        stderr_low = out['stderr'].lower()
        if ('-1743' in stderr_low
                or 'not authorized to send apple events' in stderr_low
                or 'not allowed assistive access' in stderr_low):
            out['status'] = 'denied'
        else:
            out['status'] = 'silent'
        return out
    except subprocess.TimeoutExpired:
        out['status'] = 'timeout'
        return out
    except Exception as e:
        out['stderr'] = 'exception: ' + str(e)
        return out


# ── NATIVE SCRIPTING (`applescript` action) ─────────────────────────────────

def run_native_script(script, timeout=30.0):
    """Run an AppleScript and return stdout. Raises TimeoutError on timeout,
    RuntimeError on non-zero exit. Used by runner.py to implement the
    `applescript` action."""
    if isinstance(script, list):
        script = '\n'.join(str(x) for x in script)
    try:
        r = subprocess.run(
            [_OSASCRIPT_BIN, '-e', script],
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError('AppleScript timed out after ' + str(timeout) + 's')
    if r.returncode != 0:
        err = (r.stderr or '').strip() or 'osascript exit ' + str(r.returncode)
        raise RuntimeError('AppleScript failed: ' + err)
    return (r.stdout or '').strip()


def get_real_modifier_state():
    """Stub on macOS: pynput on macOS is reliable about modifier state, so we
    don't need to second-guess it. The cross-platform recorder calls this
    every key press; returning None means 'no override, trust active_modifiers
    as-is.' (Returning an empty set here would FORCE-CLEAR active_modifiers
    every press, which is wrong on Mac.)"""
    return None
