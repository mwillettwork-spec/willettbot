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

# Cross-platform helpers — auto-dispatches to platform_mac / platform_win /
# platform_linux based on sys.platform. Single source of truth for every
# OS-specific call (frontmost app, window raising, file opens, etc.).
import platform_helpers as platform


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


# ── FRONTMOST-APP GUARD ──────────────────────────────────────────────────────
# Recorded clicks carry an optional `app` hint (the app that was frontmost
# when the user clicked). On replay, if something else is frontmost — because
# a window moved, a notification stole focus, or another app was launched —
# a blind click at the original (x,y) would land on the wrong thing. This
# helper re-focuses the expected app first so the click actually lands where
# the user meant it to.
#
# All platform-specific work (osascript on macOS, pywin32 on Windows, xdotool
# on Linux) lives in platform_helpers / platform_<os>.py. This file just
# orchestrates the steps.

def _ensure_frontmost_app(action):
    """Refocus the expected app (and, if we have it, the expected window)
    before a click. Silent no-op if no hint on the action — or if the
    platform backend can't query the foreground state. Better a possibly-
    miss-clicked replay than an aborted one.

    Sheet-handling: if the target app has a modal dialog (macOS sheets, any
    Windows owned popup) and something else is frontmost, the dialog can be
    dimmed or behind another window and clicks won't reliably fire. We try
    to raise the dialog explicitly."""
    expected = action.get('app')
    expected_title = action.get('window_title')
    if not expected and not expected_title:
        return
    current = platform.get_frontmost_app()
    if expected and (not current or current != expected):
        emit({'event': 'log',
              'message': 'Refocusing ' + expected + ' (was ' + (current or '?') + ')'})
        # Two-pronged activation: open_app (launches if not running) +
        # activate_app (stronger when the app is already running but lost
        # focus to a modal dialog from another process).
        try:
            platform.open_app(expected, timeout=3)
        except Exception:
            return
        platform.activate_app(expected)
        # Poll until the swap actually happens rather than sleeping blindly.
        # 1.2s is generous enough for cold-start Preview / Calendar / etc.
        if not platform.wait_until_frontmost(expected, timeout=1.2):
            emit({'event': 'log',
                  'message': 'Warning: ' + expected + ' did not come to '
                             'front within 1.2s — clicking anyway.'})
    # If the app is running but has a dimmed modal dialog, raising its parent
    # un-dims it so the click actually lands on a live button.
    if expected:
        if platform.focus_modal_dialog(expected):
            emit({'event': 'log',
                  'message': 'Raised modal dialog in ' + expected})
            # Dialogs animate in — short settle keeps the click from racing
            # the animation and landing on the parent window instead.
            time.sleep(0.18)
    # Even if the app was already frontmost, try to raise the exact window.
    # Gracefully degrades when Accessibility isn't granted or title changed.
    if expected_title and expected:
        if platform.raise_window_by_title(expected, expected_title):
            emit({'event': 'log',
                  'message': 'Raised window "' + expected_title[:48] + '"'})
            time.sleep(0.12)


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
        # Cross-platform: open_app launches OR focuses the app, whichever is
        # appropriate for the platform's launching primitive.
        platform.open_app(name)

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

        # In-place file-manager navigation: retarget the front window to the
        # new folder instead of spawning a new one. Only attempted for local
        # directories. Falls back to a fresh open if the platform backend
        # returns False (no front file-manager window, or no API support).
        if in_place and is_dir:
            if platform.open_directory_in_place(path):
                emit({'event': 'log',
                      'message': 'Navigated ' + platform.get_file_manager_name() +
                                 ' → ' + path})
                return
            # Fall through to plain open on backend failure.

        ok, err = platform.open_file(path, app=app)
        if not ok:
            raise RuntimeError('open_file failed: ' + err)
        emit({'event': 'log',
              'message': 'Opened ' + path + (' with ' + app if app else '')})

    elif kind == 'wait_for_app':
        # Poll the OS for the frontmost app until it matches, or timeout.
        # Useful when you want the human to manually switch to an app before
        # the script continues (instead of the script force-focusing it).
        target = substitute(action.get('name', ''), variables)
        timeout = float(action.get('timeout', 60))
        deadline = time.time() + timeout
        while time.time() < deadline:
            front = platform.get_frontmost_app()
            if front == target:
                emit({'event': 'log', 'message': 'Detected ' + target + ' is now frontmost.'})
                return
            time.sleep(0.3)
        raise TimeoutError('Timed out waiting for "' + target + '" to be frontmost.')

    elif kind == 'applescript' or kind == 'powershell' or kind == 'shell' or kind == 'native_script':
        # Run a native script in the platform's scripting language:
        #   macOS   → AppleScript (osascript)
        #   Windows → PowerShell
        #   Linux   → bash
        # All four action names are accepted so a script saved on one OS still
        # parses on another (it'll just fail at run time if the body is in
        # the wrong language). Variables are substituted first; use the
        # "|json" filter when injecting values into string literals so quotes
        # and newlines are escaped properly.
        raw = action.get('script', '')
        if isinstance(raw, list):
            raw = '\n'.join(str(x) for x in raw)
        script = substitute(raw, variables)
        timeout = float(action.get('timeout', 30))
        # Cross-OS sanity check: warn if the script's "expected language"
        # doesn't match the running platform. Doesn't block — there's nothing
        # stopping the user from invoking osascript via a shell action on Mac
        # — but it surfaces the most common mistake (running a Mac script on
        # Windows or vice versa).
        if kind != 'native_script' and kind != platform.NATIVE_SCRIPT_ACTION:
            emit({'event': 'log',
                  'message': 'Note: this script step expects ' + kind +
                             ' but this machine runs ' + platform.PLATFORM_NAME +
                             ' (native: ' + platform.NATIVE_SCRIPT_ACTION + '). '
                             'Continuing anyway.'})
        out = platform.run_native_script(script, timeout=timeout)
        store_as = action.get('storeAs')
        if store_as:
            variables[store_as] = out
        if out:
            label = platform.NATIVE_SCRIPT_ACTION.capitalize()
            emit({'event': 'log', 'message': label + ' → ' + out[:200]})

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
    """True iff this binary is granted permission to post mouse / keyboard
    events. Without this, pyautogui calls silently no-op (on macOS — Windows
    and Linux have no equivalent permission gate). Returns None when we
    can't probe (treated as ok by the caller)."""
    if not platform.NEEDS_ACCESSIBILITY_GRANT:
        return None   # No grant required on this platform.
    return platform.check_accessibility()


_ACTIONS_NEEDING_A11Y = {
    'click', 'double_click', 'right_click', 'drag',
    'move_to', 'scroll', 'type_text', 'hotkey', 'press_key',
    'mouse_down', 'mouse_up',
}


def run_script(script):
    variables = dict(script.get('variables', {}))
    actions = script.get('actions', [])
    name = script.get('name', 'Untitled')

    # Built-in platform variables — these auto-resolve to the right per-OS
    # value so cross-platform seed scripts can use {{TEXT_EDITOR}},
    # {{FILE_MANAGER}}, {{MOD}} etc. without a per-OS branch in the JSON.
    # User-defined variables take precedence (we only fill defaults).
    _platform_defaults = {
        'TEXT_EDITOR':  'TextEdit'   if platform.is_mac() else
                        'Notepad'    if platform.is_windows() else
                        'gedit',
        'FILE_MANAGER': platform.get_file_manager_name(),  # Finder | Explorer | Files
        # Logical "primary modifier" for shortcuts — Cmd on Mac, Ctrl elsewhere.
        # Lets a script say {{MOD}}+n for new-document and have it work on both.
        'MOD':          'command'    if platform.is_mac() else 'ctrl',
        'PLATFORM':     platform.PLATFORM_NAME,
    }
    for k, v in _platform_defaults.items():
        variables.setdefault(k, v)

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
