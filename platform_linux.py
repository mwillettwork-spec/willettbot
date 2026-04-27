# Copyright (c) 2026 Myles Willett. All rights reserved.
# Proprietary and confidential. No reproduction, distribution, or use
# without express written permission.

"""
WillettBot Platform Backend — Linux (X11)
==========================================
Implements the platform_helpers public API on Linux using:
  - xdotool         — frontmost window queries + window raising
  - wmctrl          — window listing fallback
  - xdg-open        — open file with default app
  - bash            — for the native_script action

REQUIRED system packages (the installer will warn if missing):
    sudo apt install xdotool wmctrl

WAYLAND CAVEAT: xdotool / wmctrl are X11-specific. On Wayland sessions
(modern GNOME default) these will return empty results — recording still
captures clicks/keystrokes via pynput, replay still works via pyautogui's
XTEST/uinput backend, but the app-context detection (frontmost app, file
manager selection) silently degrades. We emit '' on every query rather than
raising so the recorder stays usable.

FILE-MANAGER DETECTION: There's no single "file manager" on Linux. We probe
the frontmost window's WM_CLASS to detect Nautilus (GNOME Files), Dolphin
(KDE), Thunar (XFCE), Nemo (Cinnamon), and PCManFM (LXDE). For each we try
their D-Bus / shell hooks for current path + selection, but that surface is
patchy. Best-effort.

UNTESTED — no Linux test machine right now. Scaffolding only.
"""

import os
import sys
import time
import shutil
import subprocess


# ── PLATFORM CONSTANTS ──────────────────────────────────────────────────────
PLATFORM_NAME             = 'Linux'
NATIVE_SCRIPT_ACTION      = 'shell'
NEEDS_ACCESSIBILITY_GRANT = False   # No permission gate; just X11 access


# ── INTERNAL: command runners ───────────────────────────────────────────────

def _run(cmd, timeout=2.0):
    """Run a command, return stdout stripped or '' on any failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return ''
        return (r.stdout or '').strip()
    except Exception:
        return ''


def _run_full(cmd, timeout=2.0):
    """Run a command, return (rc, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip())
    except subprocess.TimeoutExpired:
        return (-1, '', 'timeout')
    except Exception as e:
        return (-1, '', str(e))


def _have(binary):
    """True iff `binary` is on PATH."""
    return shutil.which(binary) is not None


def _is_wayland():
    """Crude Wayland detection — most window queries no-op there."""
    return os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland' or \
           bool(os.environ.get('WAYLAND_DISPLAY'))


# ── QUERIES ─────────────────────────────────────────────────────────────────

def get_frontmost_app(timeout=2.0):
    """Friendly name of the active window's app, derived from WM_CLASS.
    'firefox' → 'Firefox', 'org.gnome.Nautilus' → 'Files', etc."""
    if not _have('xdotool'):
        return ''
    # xdotool getactivewindow getwindowclassname → e.g. 'Navigator' (Firefox),
    # 'gnome-terminal-server', 'org.gnome.Nautilus'. Then we map known names.
    cls = _run(['xdotool', 'getactivewindow', 'getwindowclassname'], timeout=timeout)
    if not cls:
        return ''
    return _friendly_app_name(cls)


def _friendly_app_name(wm_class):
    """Map an X11 WM_CLASS / classname to a human-friendly app name."""
    if not wm_class:
        return ''
    base = wm_class.lower()
    known = {
        'firefox':                    'Firefox',
        'navigator':                  'Firefox',   # Firefox uses 'Navigator' for main window
        'google-chrome':              'Google Chrome',
        'chromium':                   'Chromium',
        'chromium-browser':           'Chromium',
        'microsoft-edge':             'Microsoft Edge',
        'org.gnome.nautilus':         'Files',
        'nautilus':                   'Files',
        'org.kde.dolphin':            'Dolphin',
        'dolphin':                    'Dolphin',
        'thunar':                     'Thunar',
        'nemo':                       'Nemo',
        'pcmanfm':                    'PCManFM',
        'gnome-terminal-server':      'Terminal',
        'gnome-terminal':             'Terminal',
        'org.gnome.terminal':         'Terminal',
        'konsole':                    'Konsole',
        'org.kde.konsole':            'Konsole',
        'xterm':                      'XTerm',
        'code':                       'Visual Studio Code',
        'code-oss':                   'Visual Studio Code',
        'sublime_text':               'Sublime Text',
        'gedit':                      'Text Editor',
        'libreoffice-writer':         'LibreOffice Writer',
        'libreoffice-calc':           'LibreOffice Calc',
        'libreoffice-impress':        'LibreOffice Impress',
        'slack':                      'Slack',
        'discord':                    'Discord',
        'spotify':                    'Spotify',
        'thunderbird':                'Thunderbird',
        'evolution':                  'Evolution',
    }
    if base in known:
        return known[base]
    # Fall back: strip common prefixes, title-case
    for prefix in ('org.gnome.', 'org.kde.', 'com.', 'org.'):
        if base.startswith(prefix):
            base = base[len(prefix):]
    return base.capitalize()


def get_frontmost_window_title(timeout=2.0):
    """Title of the active window."""
    if not _have('xdotool'):
        return ''
    return _run(['xdotool', 'getactivewindow', 'getwindowname'], timeout=timeout)


def get_window_count(app_name, timeout=2.0):
    """Approximate window count for `app_name`. Uses wmctrl -lx and matches
    the 4th column (WM_CLASS). Returns None on failure."""
    if not _have('wmctrl') or not app_name:
        return None
    out = _run(['wmctrl', '-lx'], timeout=timeout)
    if not out:
        return None
    count = 0
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        wm_class = parts[2]   # e.g. 'Navigator.firefox'
        # WM_CLASS in wmctrl output is "instance.class" — match either half.
        instance, _, klass = wm_class.partition('.')
        friendly = _friendly_app_name(klass) or _friendly_app_name(instance)
        if friendly == app_name:
            count += 1
    return count


def get_file_manager_name():
    """The "file manager" varies by desktop. Return whichever one is the
    frontmost (Nautilus, Dolphin, Thunar, Nemo, PCManFM), or 'Files' as a
    generic fallback."""
    front = get_frontmost_app()
    if front in ('Files', 'Dolphin', 'Thunar', 'Nemo', 'PCManFM'):
        return front
    return 'Files'


def get_file_manager_front_path(timeout=1.5):
    """Path of the foreground file-manager window. Best-effort — most Linux
    file managers don't expose a stable scripting API. Strategies:
      1. Window title often equals the folder name (Nautilus, Thunar, Nemo).
         We match that against the user's home directory subtree.
    Returns '' when we can't determine it."""
    title = get_frontmost_window_title()
    front = get_frontmost_app()
    if not title or front not in ('Files', 'Dolphin', 'Thunar', 'Nemo', 'PCManFM'):
        return ''
    # Many file managers show "Folder Name" as the title. Resolve it under
    # the user's home as a best-effort guess. This will miss /etc and /var
    # paths but that's fine for the typical recorder use case (mom's Desktop,
    # Downloads, Documents).
    home = os.path.expanduser('~')
    candidate = os.path.join(home, title)
    if os.path.isdir(candidate):
        return candidate
    # Try common locations.
    for root in (home, os.path.join(home, 'Desktop'), os.path.join(home, 'Documents'),
                 os.path.join(home, 'Downloads')):
        candidate = os.path.join(root, title)
        if os.path.isdir(candidate):
            return candidate
    return ''


def get_file_manager_selection(timeout=1.5):
    """File-manager selection on Linux is hard to query without per-FM
    integration. Returning '' here — the recorder gracefully handles missing
    selection data (it just won't auto-promote click→open_file). A future
    enhancement could query Nautilus's D-Bus interface for SelectedItems."""
    return ''


# ── APP / WINDOW CONTROL ────────────────────────────────────────────────────

def activate_app(app_name, timeout=2.0):
    """Bring any window of `app_name` to the front. Uses wmctrl -a which
    matches against the window title — works for most cases. For pathological
    cases (multiple windows with similar titles) we fall back to xdotool's
    --class search."""
    if not app_name:
        return False
    # wmctrl -x -a matches WM_CLASS, which is more reliable than title.
    if _have('wmctrl'):
        # Look up the right class string from our friendly map.
        reverse = {
            'Firefox': 'Navigator.firefox',
            'Google Chrome': 'google-chrome',
            'Chromium': 'chromium-browser',
            'Files': 'org.gnome.Nautilus',
            'Dolphin': 'org.kde.dolphin',
            'Thunar': 'thunar',
            'Nemo': 'nemo',
            'PCManFM': 'pcmanfm',
            'Terminal': 'gnome-terminal-server',
            'Visual Studio Code': 'code',
        }
        klass = reverse.get(app_name, app_name.lower())
        rc, _, _ = _run_full(['wmctrl', '-x', '-a', klass], timeout=timeout)
        if rc == 0:
            return True
    # Fallback: xdotool search by window name + activate.
    if _have('xdotool'):
        rc, _, _ = _run_full(
            ['xdotool', 'search', '--name', app_name, 'windowactivate'],
            timeout=timeout
        )
        return rc == 0
    return False


def raise_window_by_title(app_name, title, timeout=2.5):
    """Activate the window whose title starts with `title`."""
    if not _have('xdotool') or not title:
        return False
    title_prefix = title[:24] if len(title) > 24 else title
    # xdotool search --name supports regex; escape special chars then anchor.
    import re
    pattern = '^' + re.escape(title_prefix)
    rc, _, _ = _run_full(
        ['xdotool', 'search', '--name', pattern, 'windowactivate'],
        timeout=timeout
    )
    return rc == 0


def focus_modal_dialog(app_name, timeout=2.5):
    """Linux dialogs are independent top-level windows. If the app has a
    transient-for window, raising it is the equivalent. Best-effort: no-op
    on Linux because there's no clean cross-DE API for this."""
    return False


def wait_until_frontmost(expected, timeout=1.2):
    """Poll until `expected` is the frontmost app, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if get_frontmost_app() == expected:
            return True
        time.sleep(0.05)
    return False


def open_app(name, timeout=5.0):
    """Launch an app by name — fall back to running the lower-cased name as
    a binary (works for most desktop apps installed via apt)."""
    if not name:
        return
    # Try the friendly-to-binary map first.
    reverse = {
        'Firefox': 'firefox',
        'Google Chrome': 'google-chrome',
        'Chromium': 'chromium',
        'Files': 'nautilus',
        'Dolphin': 'dolphin',
        'Thunar': 'thunar',
        'Nemo': 'nemo',
        'PCManFM': 'pcmanfm',
        'Terminal': 'gnome-terminal',
        'Konsole': 'konsole',
        'Visual Studio Code': 'code',
        'Slack': 'slack',
        'Discord': 'discord',
        'Spotify': 'spotify',
    }
    binary = reverse.get(name, name.lower().replace(' ', '-'))
    if not _have(binary):
        # Try `xdg-open` with the literal name as a fallback. Won't always
        # work but better than nothing.
        return
    try:
        # Detached so we don't block on the spawned process.
        subprocess.Popen([binary], start_new_session=True)
    except Exception:
        pass


def open_file(path, app='', timeout=10.0):
    """Open a file (or URL) with the default app, or with `app` if given."""
    if not path:
        return (False, 'open_file: empty path')
    try:
        if app:
            reverse = {
                'Firefox': 'firefox',
                'Google Chrome': 'google-chrome',
                'Visual Studio Code': 'code',
                'Files': 'nautilus',
            }
            binary = reverse.get(app, app.lower().replace(' ', '-'))
            r = subprocess.run([binary, path],
                               capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                err = (r.stderr or '').strip() or binary + ' exit ' + str(r.returncode)
                return (False, err)
            return (True, '')
        # No specific app — use xdg-open for files OR URLs.
        if not _have('xdg-open'):
            return (False, 'xdg-open not installed')
        r = subprocess.run(['xdg-open', path],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr or '').strip() or 'xdg-open exit ' + str(r.returncode)
            return (False, err)
        return (True, '')
    except subprocess.TimeoutExpired:
        return (False, 'open timed out after ' + str(timeout) + 's')
    except Exception as e:
        return (False, str(e))


def open_directory_in_place(path, timeout=5.0):
    """No portable way to retarget a file-manager window on Linux — each FM
    has its own (and most don't expose one cleanly). Returning False so the
    caller falls back to a fresh open. Future: use D-Bus
    org.freedesktop.FileManager1.ShowFolders for compliant FMs."""
    return False


# ── PERMISSION PROBES ───────────────────────────────────────────────────────

def check_accessibility():
    """Linux has no permission gate for input simulation (running as the
    user is sufficient). Return True so upstream UI doesn't show a banner."""
    return True


def check_automation():
    """Same as accessibility — no gate."""
    return True


def probe_automation_permission():
    """Recorder-startup probe — confirm xdotool is installed (otherwise
    context detection will be entirely empty). Wayland sessions get a
    diagnostic note since most window queries silently fail there."""
    if _is_wayland():
        return {
            'status': 'silent', 'returncode': 0, 'stdout': '',
            'stderr': 'Wayland session — window queries unavailable. '
                      'Recording works, but app-context detection is disabled.',
            'osascript': True
        }
    if not _have('xdotool'):
        return {
            'status': 'error', 'returncode': None,
            'stdout': '',
            'stderr': 'xdotool not installed. Run: sudo apt install xdotool wmctrl',
            'osascript': False
        }
    # Smoke test: query the active window class. If it returns something,
    # the X11 session is healthy.
    cls = _run(['xdotool', 'getactivewindow', 'getwindowclassname'], timeout=3.0)
    if cls:
        return {
            'status': 'ok', 'returncode': 0, 'stdout': cls, 'stderr': '',
            'osascript': True
        }
    return {
        'status': 'silent', 'returncode': 0, 'stdout': '',
        'stderr': 'xdotool ran but returned nothing — X session may not be '
                  'reachable (Wayland? remote SSH without DISPLAY?).',
        'osascript': True
    }


# ── NATIVE SCRIPTING (`shell` action) ───────────────────────────────────────

def run_native_script(script, timeout=30.0):
    """Run a bash script and return stdout. Raises TimeoutError on timeout,
    RuntimeError on non-zero exit."""
    if isinstance(script, list):
        script = '\n'.join(str(x) for x in script)
    try:
        r = subprocess.run(
            ['bash', '-c', script],
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError('Shell script timed out after ' + str(timeout) + 's')
    if r.returncode != 0:
        err = (r.stderr or '').strip() or 'bash exit ' + str(r.returncode)
        raise RuntimeError('Shell script failed: ' + err)
    return (r.stdout or '').strip()
