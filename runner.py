# Copyright (c) 2026 Myles Willett. All rights reserved.
# Proprietary and confidential. No reproduction, distribution, or use
# without express written permission.

"""
WillettBot Script Runner
========================
Executes a JSON "script" — an ordered list of actions that drive the mouse,
keyboard, and system apps. Communicates with the Electron parent over stdout
(JSON event lines) and stdin (JSON prompt responses).

Run as:  python3 runner.py path/to/script.json

Script shape:
  {
    "name": "My automation",
    "description": "What this does",
    "variables": { "foo": "bar" },
    "actions": [
      { "action": "say", "message": "Hello {{foo}}" },
      ...
    ]
  }
"""

import sys
import json
import time
import uuid
import re
import threading
import subprocess
import traceback
import urllib.parse
import os

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05  # tiny default pause between calls for reliability
except Exception as _e:
    print(json.dumps({
        'event': 'error',
        'message': 'pyautogui import failed: ' + str(_e) +
                   '. Install it with:  python3 -m pip install --user pyautogui'
    }), flush=True)
    sys.exit(0)


# ── EVENT PROTOCOL ───────────────────────────────────────────────────────────

def emit(evt):
    """Print one JSON event line on stdout for the Electron parent."""
    try:
        print(json.dumps(evt), flush=True)
    except Exception:
        pass


# ── STDIN: PROMPT RESPONSES FROM THE UI ─────────────────────────────────────
# When the runner emits a {"event":"prompt","id":X,...}, it pauses and waits
# for a matching {"id": X, "value": "...", "cancelled": false} line on stdin.

_responses = {}
_responses_lock = threading.Lock()


def _stdin_reader():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if isinstance(msg, dict) and 'id' in msg:
                with _responses_lock:
                    _responses[msg['id']] = msg
        except Exception:
            pass


threading.Thread(target=_stdin_reader, daemon=True).start()


def wait_for_response(prompt_id, timeout=600):
    """Block until the UI sends a response matching prompt_id, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _responses_lock:
            if prompt_id in _responses:
                return _responses.pop(prompt_id)
        time.sleep(0.1)
    return None


# ── FRIENDLY-ERROR TRANSLATOR ───────────────────────────────────────────────
# Turn raw Python exceptions into one-sentence explanations a non-technical
# user can act on. The recovery modal shows BOTH the friendly message and
# the original exception text — friendly is the headline, raw is the detail
# the user can copy-paste back to support if they need to.

def _friendly_error_message(exc, action):
    """Best-effort plain-English description of why the step failed."""
    raw = str(exc)
    kind = action.get('action', 'step')

    # FileNotFoundError — most common, fires from open_file when a recorded
    # path is gone. Mom moves files around constantly; this is the #1 cause
    # of broken replays.
    if isinstance(exc, FileNotFoundError):
        # The exception text usually already contains the path; pull it out.
        path = ''
        if 'path does not exist:' in raw:
            path = raw.split('path does not exist:', 1)[1].strip()
        if path:
            return ("Couldn't find '" + path + "'. The file or folder may have "
                    "been moved, renamed, or deleted since this script was "
                    "recorded.")
        return "Couldn't find a file or folder this step was supposed to open."

    if isinstance(exc, TimeoutError):
        if kind == 'wait_for_app':
            return ("The app this script was waiting for never came to the "
                    "front. It might not be installed, or it took too long "
                    "to launch.")
        if kind == 'prompt':
            return "Nobody responded to a prompt for 10 minutes."
        return "This step took too long to complete and timed out."

    if isinstance(exc, RuntimeError):
        if 'open_file failed' in raw:
            return ("Couldn't open that file. The default app may be missing, "
                    "or the file format isn't supported.")
        if 'AppleScript failed' in raw or 'osascript' in raw:
            return ("An automation step against another app didn't work. "
                    "macOS may have revoked Automation permission for that "
                    "app — check System Settings → Privacy & Security → "
                    "Automation.")
        if 'cancelled at prompt' in raw:
            return "You cancelled at a prompt earlier."

    if isinstance(exc, ValueError):
        if kind == 'hotkey' and 'keys' in raw:
            return ("This hotkey step is missing the keys to press. The "
                    "script may have been edited incorrectly.")
        return "This step's settings look wrong: " + raw

    # Catch-all — show the exception type + message.
    return type(exc).__name__ + ': ' + raw


# ── VARIABLE SUBSTITUTION ────────────────────────────────────────────────────
# Supports:
#   {{name}}         raw value (no transform)
#   {{name|url}}     URL-encoded (for injecting into URLs / query strings)
#   {{name|json}}    JSON-escaped (without outer quotes)
# Unknown filters fall back to raw. Unknown variable names pass through
# unchanged, which is useful for templating files that contain literal
# "{{" sequences you don't want substituted.

_VAR_TOKEN_RE = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|\s*([A-Za-z_][A-Za-z0-9_]*)\s*)?\}\}')


def _apply_filter(raw, filt):
    if filt is None or filt == 'raw':
        return raw
    if filt == 'url':
        return urllib.parse.quote(raw, safe='')
    if filt == 'json':
        # json.dumps returns a string with outer quotes — strip them.
        return json.dumps(raw)[1:-1]
    # Unknown filter: be permissive, just return raw.
    return raw


def substitute(value, variables):
    """Replace {{name}} and {{name|filter}} tokens recursively."""
    if isinstance(value, str):
        def _repl(m):
            name = m.group(1)
            filt = m.group(2)
            if name not in variables:
                return m.group(0)  # leave unresolved tokens alone
            return _apply_filter(str(variables[name]), filt)
        return _VAR_TOKEN_RE.sub(_repl, value)
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    return value


# ═════════════════════════════════════════════════════════════════════════════
# ════════════════════ PLATFORM-SPECIFIC SECTION: macOS ═══════════════════════
# ═════════════════════════════════════════════════════════════════════════════
#
# Every helper between this banner and the matching "END PLATFORM-SPECIFIC"
# banner below uses macOS-only APIs (osascript / AppleScript / `open`).
# When porting to Windows / Linux, replace this entire section with platform
# equivalents — the public function signatures and return shapes are stable
# so callers in run_action() don't need to change.
#
# Public surface of this section (keep these names + signatures on every port):
#   _current_frontmost_app() -> str                 # name of frontmost app
#   _raise_window_by_title(app, title) -> bool      # raise window by title prefix
#   _wait_until_frontmost(expected, timeout) -> bool# poll until app is frontmost
#   _activate_app_via_events(app) -> bool           # force app to front
#   _focus_modal_sheet(app) -> bool                 # un-dim a modal dialog
#   _ensure_frontmost_app(action)                   # called before every click
#
# Windows port notes (for next-week work):
#   - `osascript` calls → use pywin32 (win32gui, win32process) or UIAutomation.
#   - `open -a "<App>"` → use `os.startfile()` for files, or
#     subprocess.Popen(['cmd', '/c', 'start', '', appname]) for apps.
#   - Frontmost detection: win32gui.GetForegroundWindow() +
#     win32process.GetWindowThreadProcessId().
#   - Window raising: win32gui.SetForegroundWindow() (with the AttachThreadInput
#     dance for reliability across processes — Windows fights you on this).
#   - Modal sheets don't exist on Windows — dialogs are separate windows.
#     _focus_modal_sheet() can no-op there.
# ═════════════════════════════════════════════════════════════════════════════

# ── FRONTMOST-APP GUARD ──────────────────────────────────────────────────────
# Recorded clicks carry an optional `app` hint (the app that was frontmost
# when the user clicked). On replay, if something else is frontmost — because
# a window moved, a notification stole focus, or another app was launched —
# a blind click at the original (x,y) would land on the wrong thing. This
# helper re-focuses the expected app first so the click actually lands where
# the user meant it to.

def _current_frontmost_app():
    """Return the name of the frontmost macOS app, or '' on failure."""
    try:
        r = subprocess.run(
            ['osascript', '-e',
             'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0:
            return (r.stdout or '').strip()
    except Exception:
        pass
    return ''


def _raise_window_by_title(app_name, title):
    """Ask System Events to bring the window with the matching title to the
    front of `app_name`. We match by prefix because macOS titles often get a
    status suffix ('— Edited', '— file.txt') that changes across sessions but
    the stable head is still a useful anchor. Returns True on best-effort
    success, False if nothing matched (caller falls back to plain app focus).

    Why this matters: the pure `open -a <app>` call only guarantees the app is
    frontmost — NOT which of its windows is topmost. A click at (x,y) that
    was recorded in window A can still land inside window B. Raising the
    right window first closes that gap without needing pixel geometry."""
    if not app_name or not title:
        return False
    # AppleScript string escapes: backslash then double-quote.
    def esc(s):
        return s.replace('\\', '\\\\').replace('"', '\\"')
    t_full = esc(title)
    # Prefix is the first ~24 chars; it's enough to disambiguate most docs
    # while tolerating a trailing "- Edited" / tab-count suffix.
    t_prefix = esc(title[:24] if len(title) > 24 else title)
    script = '\n'.join([
        'tell application "System Events"',
        '  tell process "' + esc(app_name) + '"',
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
    try:
        r = subprocess.run(['osascript', '-e', script],
                           capture_output=True, text=True, timeout=2.5)
        return (r.returncode == 0 and (r.stdout or '').strip() == 'ok')
    except Exception:
        return False


def _wait_until_frontmost(expected, timeout=1.2):
    """Poll until `expected` is the frontmost app, or timeout. Returns True on
    success. Used instead of a fixed sleep after `open -a` because app-launch
    time varies hugely (cold-start Preview can take >1s; a warm Finder is
    <50ms), and a fixed 0.35s sleep was both too slow (wasted time) and
    sometimes too fast (clicks landing on a dialog's parent app before the
    Window Server actually finished swapping focus)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _current_frontmost_app() == expected:
            return True
        time.sleep(0.05)
    return False


def _activate_app_via_events(app_name):
    """Force `app_name` frontmost using System Events. Stronger than `open -a`
    when the app is already running but a modal sheet from another process
    grabbed focus (e.g. a macOS 'Allow access' prompt from CoreServicesUIAgent
    dims the target app's window). `tell application X to activate` walks
    through the WindowServer's focus rules properly instead of relying on
    launch-services heuristics."""
    if not app_name:
        return False
    def esc(s): return s.replace('\\', '\\\\').replace('"', '\\"')
    script = 'tell application "' + esc(app_name) + '" to activate'
    try:
        r = subprocess.run(['osascript', '-e', script],
                           capture_output=True, text=True, timeout=2)
        return r.returncode == 0
    except Exception:
        return False


def _focus_modal_sheet(app_name):
    """If `app_name` has a modal sheet attached to any window, raise that
    window so the sheet's buttons are clickable. macOS sheets (like the
    'Keep Both / Replace' file-copy dialog) can't be AXRaised directly —
    they're always attached to a parent window — but if the parent isn't
    topmost, the sheet appears dimmed ("greyed out a lil bit") and clicks
    may not register reliably. Returns True if a sheet was found + raised.

    Bug this fixes: user records clicking "Replace" in a copy-dialog; on
    replay, pyautogui fires a click at the recorded pixel, but between the
    preceding action and the click the dialog's parent window lost focus
    (e.g. Safari notification stole it), so the sheet is dimmed and the
    button press doesn't fire."""
    if not app_name:
        return False
    def esc(s): return s.replace('\\', '\\\\').replace('"', '\\"')
    # Walk every window of the process; if it has a sheet, AXRaise it and
    # make the process frontmost. Returns 'raised' on success, 'none' if no
    # sheet was found, 'err:<msg>' on failure.
    script = '\n'.join([
        'tell application "System Events"',
        '  tell process "' + esc(app_name) + '"',
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
    try:
        r = subprocess.run(['osascript', '-e', script],
                           capture_output=True, text=True, timeout=2.5)
        return r.returncode == 0 and (r.stdout or '').strip() == 'raised'
    except Exception:
        return False


def _ensure_frontmost_app(action):
    """Refocus the expected app (and, if we have it, the expected window)
    before a click. Silent no-op if no hint on the action — or if we can't
    query osascript. Better a possibly-miss-clicked replay than an aborted
    one.

    Sheet-handling is important here: if the target app has a modal dialog
    (the 'Replace / Keep Both' copy prompts, 'Allow access' permission
    dialogs, save-file sheets, etc.) and something else is frontmost, the
    dialog renders dimmed and clicks into it don't reliably fire. We detect
    this case and explicitly raise the sheet's parent window."""
    expected = action.get('app')
    expected_title = action.get('window_title')
    if not expected and not expected_title:
        return
    current = _current_frontmost_app()
    if expected and (not current or current != expected):
        emit({'event': 'log',
              'message': 'Refocusing ' + expected + ' (was ' + (current or '?') + ')'})
        # Two-pronged activation: `open -a` (launches if not running) +
        # AppleScript `activate` (stronger when the app is already running
        # but loses focus to a modal sheet from another process).
        try:
            subprocess.run(['open', '-a', expected], check=False, timeout=3)
        except Exception:
            return
        _activate_app_via_events(expected)
        # Poll until the swap actually happens rather than sleeping blindly.
        # 1.2s is generous enough for cold-start Preview / Calendar / etc.
        if not _wait_until_frontmost(expected, timeout=1.2):
            emit({'event': 'log',
                  'message': 'Warning: ' + expected + ' did not come to '
                             'front within 1.2s — clicking anyway.'})
    # If the app is running but has a dimmed modal sheet, raising its parent
    # window un-dims it so the click actually lands on a live button.
    if expected:
        if _focus_modal_sheet(expected):
            emit({'event': 'log',
                  'message': 'Raised modal dialog in ' + expected})
            # Sheets animate in — a short settle keeps the click from racing
            # the animation and landing on the parent window instead.
            time.sleep(0.18)
    # Even if the app was already frontmost, try to raise the exact window.
    # Gracefully degrades when Accessibility isn't granted or title changed.
    if expected_title and expected:
        raised = _raise_window_by_title(expected, expected_title)
        if raised:
            emit({'event': 'log',
                  'message': 'Raised window "' + expected_title[:48] + '"'})
            time.sleep(0.12)


# ═════════════════════════════════════════════════════════════════════════════
# ══════════════════ END PLATFORM-SPECIFIC SECTION (macOS) ════════════════════
# ═════════════════════════════════════════════════════════════════════════════


# ── ACTION DISPATCH ──────────────────────────────────────────────────────────

def run_action(action, variables):
    """Execute one action. May raise; caller logs + aborts on error."""
    kind = action.get('action')

    if kind == 'say':
        emit({'event': 'log', 'message': substitute(action.get('message', ''), variables)})

    elif kind == 'wait':
        seconds = float(action.get('seconds', 1))
        # Sleep in slices so stop signals can interrupt long waits.
        remaining = seconds
        while remaining > 0:
            t = 0.1 if remaining > 0.1 else remaining
            time.sleep(t)
            remaining -= t

    elif kind == 'open_app' or kind == 'focus_app':
        name = substitute(action.get('name', ''), variables)
        # PLATFORM:macOS — `open -a "AppName"` launches the app OR brings it to
        # the front if running. Works for both "open" and "re-focus" use cases.
        # WIN-PORT: replace with subprocess.Popen(['cmd','/c','start','',name])
        # for launching, plus pywin32 SetForegroundWindow for re-focus.
        subprocess.run(['open', '-a', name], check=False)

    elif kind == 'open_file':
        # Open a file (or folder) with its default app — or with a specific
        # app if "app" is provided. Way more reliable than asking the user to
        # Finder-and-double-click it.
        #   { "action": "open_file", "path": "~/Desktop/report.pdf" }
        #   { "action": "open_file", "path": "~/Downloads/foo.png", "app": "Preview" }
        #   { "action": "open_file", "path": "~/Projects", "inPlace": true }
        # macOS `open` handles URLs too (http://, file://, mailto:, etc.).
        # When inPlace=true AND path is a directory, we retarget the front
        # Finder window instead of spawning a new one — this stops the "ten
        # Finder windows pile up on replay" problem during folder drilling.
        raw_path = substitute(action.get('path', ''), variables)
        app      = substitute(action.get('app', ''),  variables)
        in_place = bool(action.get('inPlace', False))
        if not raw_path:
            raise ValueError('open_file needs a "path"')
        # Expand ~ and $HOME so users can type "~/Desktop/file.pdf" without
        # knowing their username. URLs (http://, file://, etc.) pass through.
        is_url = '://' in raw_path
        path = raw_path if is_url else os.path.expanduser(os.path.expandvars(raw_path))
        if not is_url and not os.path.exists(path):
            raise FileNotFoundError('open_file: path does not exist: ' + path)

        # ── DIRECTORY SAFETY NET ─────────────────────────────────────────
        # Folders should ALWAYS go through Finder, never through an app hint.
        # This prevents a nasty class of bug where the recorder accidentally
        # stamped `app: "Google Chrome"` on a folder-open action (because
        # Chrome happened to be frontmost when the poller sampled). On replay,
        # `open -a "Google Chrome" /Users/mom/Desktop` cheerfully loads the
        # folder as a file:// URL in a Chrome tab instead of opening Finder.
        # Strip the hint, force inPlace, and let Finder do its job.
        is_dir = (not is_url) and os.path.isdir(path)
        if is_dir and app:
            emit({'event': 'log',
                  'message': 'Ignoring app hint "' + app + '" for folder — '
                             'routing to Finder instead.'})
            app = ''
        if is_dir and not in_place:
            # Default to inPlace for folders even if the action didn't say so.
            # Spawns way fewer stray Finder windows on long scripts.
            in_place = True

        # PLATFORM:macOS — In-place Finder navigation: retarget the front
        # window to the new folder instead of spawning a new one. Only
        # attempted for local directories (URLs + files still use plain
        # `open`). Falls back to plain `open` if no Finder window exists or
        # AppleScript fails.
        # WIN-PORT: equivalent is the Shell.Application COM object's Windows
        # collection — iterate, find the front Explorer window, set its
        # Navigate2 to the new path. Or just call os.startfile(path) which
        # opens a new Explorer window every time (less polished UX).
        if in_place and is_dir:
            applescript = (
                'tell application "Finder"\n'
                '  activate\n'
                '  if (count of windows) is 0 then\n'
                '    make new Finder window to (POSIX file "' + path + '" as alias)\n'
                '  else\n'
                '    set target of front window to (POSIX file "' + path + '" as alias)\n'
                '  end if\n'
                'end tell'
            )
            try:
                r = subprocess.run(
                    ['osascript', '-e', applescript],
                    capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0:
                    emit({'event': 'log',
                          'message': 'Navigated Finder → ' + path})
                    return
                # Fall through to plain `open` on AppleScript failure.
            except Exception:
                pass  # Fall through to plain `open`.

        cmd = ['open']
        if app:
            cmd += ['-a', app]
        cmd.append(path)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or '').strip() or 'open exit ' + str(result.returncode)
            raise RuntimeError('open_file failed: ' + err)
        emit({'event': 'log',
              'message': 'Opened ' + path + (' with ' + app if app else '')})

    elif kind == 'wait_for_app':
        # Poll macOS for the frontmost app until it matches, or timeout.
        # Useful when you want the human to manually switch to an app before
        # the script continues (instead of the script force-focusing it).
        target = substitute(action.get('name', ''), variables)
        timeout = float(action.get('timeout', 60))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = subprocess.run(
                    ['osascript', '-e',
                     'tell application "System Events" to get name of first application process whose frontmost is true'],
                    capture_output=True, text=True, timeout=3
                )
                front = r.stdout.strip()
            except Exception:
                front = ''
            if front == target:
                emit({'event': 'log', 'message': 'Detected ' + target + ' is now frontmost.'})
                return
            time.sleep(0.3)
        raise TimeoutError('Timed out waiting for "' + target + '" to be frontmost.')

    elif kind == 'applescript':
        # PLATFORM:macOS — Run an AppleScript via osascript. Way more reliable
        # than simulated keystrokes for Mac-app automation (new tabs, window
        # ordering, etc.). Script can be a single string or a list of lines.
        # Variables are substituted in first. Use the "|json" filter when
        # injecting values into string literals so quotes and newlines are
        # escaped properly:
        #   "make new tab with properties {URL:\"{{url|json}}\"}"
        # WIN-PORT: add a parallel `powershell` action with the same shape;
        # this `applescript` action stays Mac-only and would error on Windows.
        raw = action.get('script', '')
        if isinstance(raw, list):
            raw = '\n'.join(str(x) for x in raw)
        script = substitute(raw, variables)
        timeout = float(action.get('timeout', 30))
        try:
            r = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError('AppleScript timed out after ' + str(timeout) + 's')
        if r.returncode != 0:
            err = (r.stderr or '').strip() or 'osascript exit ' + str(r.returncode)
            raise RuntimeError('AppleScript failed: ' + err)
        out = (r.stdout or '').strip()
        store_as = action.get('storeAs')
        if store_as:
            variables[store_as] = out
        if out:
            emit({'event': 'log', 'message': 'AppleScript → ' + out[:200]})

    elif kind == 'click':
        _ensure_frontmost_app(action)
        pyautogui.click(
            x=int(action['x']),
            y=int(action['y']),
            button=action.get('button', 'left')
        )

    elif kind == 'double_click':
        _ensure_frontmost_app(action)
        pyautogui.doubleClick(
            x=int(action['x']),
            y=int(action['y']),
            button=action.get('button', 'left')
        )

    elif kind == 'drag':
        # Drag-and-drop: jump to the start, press the button, glide to the
        # end, release. Duration controls glide time; it matches the recorder
        # so a fast flick stays fast and a slow drag stays slow.
        _ensure_frontmost_app(action)
        from_x = int(action.get('fromX', 0))
        from_y = int(action.get('fromY', 0))
        to_x   = int(action.get('toX', 0))
        to_y   = int(action.get('toY', 0))
        btn    = action.get('button', 'left')
        duration = float(action.get('duration', 0.3))
        pyautogui.moveTo(from_x, from_y)
        pyautogui.dragTo(to_x, to_y, duration=duration, button=btn)

    elif kind == 'move_to':
        # Ignore recorded `duration`: always teleport. Glides were the #1
        # cause of "replay looks janky" — when the target window has moved
        # since recording, the cursor glides visibly through empty space
        # before the next click fires. Teleport is invisible and correct.
        # The cursor still ends up at the recorded pixel so any downstream
        # hover-sensitive UI (rare in automation scripts) still gets the
        # right position before the next action runs.
        pyautogui.moveTo(int(action['x']), int(action['y']), duration=0)

    elif kind == 'type':
        text = substitute(action.get('text', ''), variables)
        pyautogui.typewrite(text, interval=float(action.get('interval', 0.02)))

    elif kind == 'press':
        key = action.get('key', 'enter')
        pyautogui.press(key)

    elif kind == 'hotkey':
        keys = action.get('keys', [])
        if not keys:
            raise ValueError('hotkey action needs a "keys" array')
        pyautogui.hotkey(*keys)

    elif kind == 'switch_desktop':
        # Mission Control's "move one space left/right" is Ctrl+←/→ by default.
        # This is what macOS does under the hood when you 3-finger swipe —
        # trackpad gestures can't be simulated but the underlying shortcut can.
        direction = str(action.get('direction', 'right')).lower()
        count = int(action.get('count', 1))
        key = 'left' if direction == 'left' else 'right'
        for _ in range(max(1, count)):
            pyautogui.hotkey('ctrl', key)
            time.sleep(0.25)  # give the space-switch animation time to settle

    elif kind == 'prompt':
        # Pause execution and ask the human something. Supports three kinds:
        #   confirm        — shows a message, user clicks Continue or Cancel
        #   input          — user types a plain value, stored into a variable
        #   secure_input   — like input but UI renders a password field and
        #                    the value is never logged or persisted
        prompt_id = str(uuid.uuid4())
        message = substitute(action.get('message', ''), variables)
        prompt_kind = action.get('kind', 'confirm')
        emit({
            'event': 'prompt',
            'id': prompt_id,
            'message': message,
            'kind': prompt_kind,
            'storeAs': action.get('storeAs'),
            'confirmLabel': action.get('confirmLabel'),
            'cancelLabel': action.get('cancelLabel')
        })
        response = wait_for_response(prompt_id)
        if response is None:
            raise TimeoutError('Prompt timed out after 10 minutes.')
        if response.get('cancelled'):
            raise RuntimeError('User cancelled at prompt: ' + message)
        if prompt_kind in ('input', 'secure_input'):
            var_name = action.get('storeAs', 'last_input')
            variables[var_name] = response.get('value', '')
            # Intentionally do NOT log the value for secure_input.

    else:
        raise ValueError('Unknown action: ' + str(kind))


# ── MAIN LOOP ────────────────────────────────────────────────────────────────

def _has_accessibility():
    """True iff this python binary is granted Accessibility (can post mouse/
    keyboard events). Without this, pyautogui calls silently no-op. Returns
    None on platforms where we can't probe (treated as ok by the caller).

    PLATFORM:macOS — uses Apple's ApplicationServices framework + AXIsProcessTrusted.
    WIN-PORT: Windows has no equivalent permission gate — input simulation
    works without a TCC-style grant. Return None on Windows so the caller
    skips the warning. (Linux: similar — just return None.)"""
    if sys.platform != 'darwin':
        return None
    try:
        import ctypes, ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library('ApplicationServices'))
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        return None


_ACTIONS_NEEDING_A11Y = {
    'click', 'double_click', 'right_click', 'drag',
    'move_to', 'scroll', 'type_text', 'hotkey', 'press_key',
    'mouse_down', 'mouse_up',
}


def run_script(script):
    variables = dict(script.get('variables', {}))
    actions = script.get('actions', [])
    name = script.get('name', 'Untitled')

    # AXIsProcessTrusted() is advisory — can return false negatives on fresh
    # unsigned binaries after a rebuild even when CGEventPost works fine. So
    # we WARN but don't block, letting pyautogui actually try. If the grant
    # is really missing, events silently no-op and the user sees no action;
    # if it's actually working, we don't falsely block a working setup.
    needs_a11y = any(a.get('action') in _ACTIONS_NEEDING_A11Y for a in actions if isinstance(a, dict))
    if needs_a11y and _has_accessibility() is False:
        emit({
            'event': 'warning',
            'code': 'accessibility-maybe-denied',
            'python_path': sys.executable,
            'message': (
                "macOS may not have granted Accessibility to this Python "
                "binary — if clicks/keystrokes don't visibly happen, open "
                "System Settings → Privacy & Security → Accessibility, "
                "remove any old 'python3' entry, then add this exact file: "
                + sys.executable
            )
        })

    emit({'event': 'script-start', 'name': name, 'total': len(actions)})

    i = 0
    while i < len(actions):
        action = actions[i]
        emit({'event': 'step', 'index': i, 'action': action.get('action'),
              'label': action.get('label') or action.get('action')})
        try:
            run_action(action, variables)
            i += 1
        except pyautogui.FailSafeException:
            # User moved the cursor to a screen corner — explicit "abort"
            # signal. No recovery dialog: this is them telling us to stop.
            emit({'event': 'failsafe', 'index': i})
            return
        except Exception as e:
            # Step failed. Instead of dying with a traceback, ask the user
            # what to do — skip, retry, or stop. The hub renders a friendly
            # modal; the choice comes back over stdin (same channel the
            # `prompt` action already uses for input). If no decision
            # arrives within 15 minutes (e.g. App was closed), default to
            # 'stop' so we don't leak a hung subprocess forever.
            err_id = str(uuid.uuid4())
            emit({
                'event': 'step-error',
                'id': err_id,
                'index': i,
                'action': action.get('action'),
                'label': action.get('label') or action.get('action'),
                'message': str(e),
                'friendly': _friendly_error_message(e, action),
                'trace': traceback.format_exc()
            })
            response = wait_for_response(err_id, timeout=900)
            choice = (response or {}).get('choice', 'stop')
            if choice == 'skip':
                emit({'event': 'log',
                      'message': '⏭ Skipped step ' + str(i + 1) + ' — continuing.'})
                i += 1
                continue
            if choice == 'retry':
                emit({'event': 'log',
                      'message': '↻ Retrying step ' + str(i + 1) + '…'})
                # Don't increment i — the while-loop runs the same action
                # again. The friendly retry usually only helps for transient
                # failures (app wasn't focused yet, file is being downloaded,
                # network blip); persistent errors will just fail again and
                # the user sees the recovery modal a second time.
                continue
            # Default / explicit 'stop'.
            emit({
                'event': 'error',
                'index': i,
                'action': action.get('action'),
                'message': str(e),
                'trace': traceback.format_exc()
            })
            return

    emit({'event': 'script-done', 'name': name})


if __name__ == '__main__':
    if len(sys.argv) < 2:
        emit({'event': 'error', 'message': 'Usage: runner.py <script.json>'})
        sys.exit(0)

    try:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            script = json.load(f)
    except Exception as e:
        emit({'event': 'error', 'message': 'Could not read script: ' + str(e)})
        sys.exit(0)

    try:
        run_script(script)
    except KeyboardInterrupt:
        emit({'event': 'stopped', 'reason': 'signal'})
    except Exception as e:
        emit({'event': 'error', 'message': 'Fatal: ' + str(e), 'trace': traceback.format_exc()})

    sys.exit(0)
