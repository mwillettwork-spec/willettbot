# Copyright (c) 2026 Myles Willett. All rights reserved.
# Proprietary and confidential. No reproduction, distribution, or use
# without express written permission.

import sys
import json
import time
import threading
import traceback

# pyautogui must import for ANYTHING to work. Emit a clean one-line
# JSON error so both the capture-position path and the start path surface
# a real message instead of a silent exit-code-1.
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0
except Exception as _e:
    print(json.dumps({
        'error': 'pyautogui import failed: ' + str(_e) +
                 ". Install it with:  python3 -m pip install --user pyautogui"
    }), flush=True)
    sys.exit(0)

# NOTE: pynput used to live here to listen for the Esc stop-hotkey. We took
# it out because (a) on macOS it requires a separate Input Monitoring grant,
# which was crashing the clicker mid-run with 'This process is not trusted'
# on installs where only Accessibility was granted, and (b) main.js now
# registers Esc as a system-wide globalShortcut which works without any
# macOS permission at all. The clicker is now permission-only dependent on
# Accessibility, which pyautogui needs for CGEventPost.


def emit(event_dict):
    """Print one JSON object per line to stdout for the Electron parent to consume."""
    try:
        print(json.dumps(event_dict), flush=True)
    except Exception:
        # Last-resort: never let a logging error take down the clicker.
        pass


def get_position():
    """Print current mouse X,Y as a single JSON line and exit."""
    try:
        x, y = pyautogui.position()
        emit({'x': int(x), 'y': int(y)})
    except Exception as e:
        emit({'error': str(e)})


def check_accessibility():
    """Returns True iff THIS Python process is allowed to post mouse/keyboard
    events. On macOS this needs the Accessibility grant via AXIsProcessTrusted —
    without it, pyautogui.click() silently drops the event (no exception, no
    log). On Windows / Linux there's no equivalent gate so this returns True.
    Returns None when we can't determine — caller treats as ok."""
    try:
        import platform_helpers as _platform
        return _platform.check_accessibility()
    except Exception:
        return None


def run_clicker(config):
    """Loop-click at (x, y) every interval seconds until stopped by hotkey,
    click-limit, time-limit, failsafe, or fatal error."""
    try:
        x = int(config.get('x', 0))
        y = int(config.get('y', 0))
        interval = float(config.get('interval', 1.0))
        button = config.get('button', 'left')
        double_click = bool(config.get('doubleClick', False))
        max_clicks = int(config.get('maxClicks', 0) or 0)
        max_duration = float(config.get('maxDuration', 0) or 0)  # seconds
        stop_key_name = str(config.get('stopKey', 'esc'))
    except Exception as e:
        emit({'event': 'error', 'message': 'Bad config: ' + str(e)})
        return

    # Clamp to safe floor so we never spin the CPU.
    if interval < 0.01:
        interval = 0.01
    if button not in ('left', 'right', 'middle'):
        button = 'left'

    # Intentionally NOT running the pre-flight accessibility probe anymore.
    # The old moveRel(1px) ground-truth test was too noisy — it false-positived
    # on cursors near screen edges, across display-scaling boundaries, and on
    # some multi-monitor setups, which meant users with perfectly working
    # permissions saw the "Reset macOS permissions" card every time they
    # pressed Start. If the grant is genuinely missing the user will see the
    # clicker counter increment with no mouse movement — and the "Clicks not
    # working? Fix permissions" link under the Start/Stop buttons is always
    # one click away.

    # stop_event is still useful — SIGTERM handler sets it, and the Electron
    # globalShortcut sends SIGTERM via scriptProc.kill() when the user hits
    # the global Esc stop-hotkey. No in-process listener needed anymore.
    stop_event = threading.Event()

    def _on_sigterm(signum, frame):
        stop_event.set()
    try:
        import signal
        signal.signal(signal.SIGTERM, _on_sigterm)
        signal.signal(signal.SIGINT, _on_sigterm)
    except Exception:
        pass  # non-posix — fall through to the while-loop default

    emit({
        'event': 'start',
        'x': x, 'y': y,
        'interval': interval,
        'button': button,
        'doubleClick': double_click,
        'maxClicks': max_clicks,
        'maxDuration': max_duration,
        'stopKey': stop_key_name,
        'hotkeyActive': True  # global Esc lives in main.js now
    })

    count = 0
    start_time = time.time()
    ended_cleanly = False

    try:
        while not stop_event.is_set():
            # Time-limit check BEFORE clicking so we honor the cutoff exactly.
            if max_duration > 0 and (time.time() - start_time) >= max_duration:
                emit({'event': 'done', 'count': count, 'reason': 'time-limit'})
                ended_cleanly = True
                break

            # The click itself — most likely failure point, catch per-iteration.
            try:
                if double_click:
                    pyautogui.doubleClick(x=x, y=y, button=button)
                else:
                    pyautogui.click(x=x, y=y, button=button)
            except pyautogui.FailSafeException:
                emit({'event': 'failsafe', 'count': count})
                ended_cleanly = True
                break
            except Exception as click_err:
                emit({
                    'event': 'error',
                    'count': count,
                    'message': 'Click failed: ' + str(click_err) +
                               '. If this is the first run, grant Accessibility permission in '
                               'System Settings → Privacy & Security → Accessibility, then restart the app.'
                })
                ended_cleanly = True
                break

            count += 1
            emit({'event': 'click', 'count': count})

            # Click-limit check.
            if max_clicks > 0 and count >= max_clicks:
                emit({'event': 'done', 'count': count, 'reason': 'click-limit'})
                ended_cleanly = True
                break

            # Sleep in small slices so the hotkey / time-limit can interrupt
            # a long interval without waiting the full duration.
            remaining = interval
            slice_s = 0.05
            while remaining > 0 and not stop_event.is_set():
                if max_duration > 0 and (time.time() - start_time) >= max_duration:
                    break
                t = slice_s if remaining > slice_s else remaining
                time.sleep(t)
                remaining -= t

        # Exited the while loop — figure out why.
        if not ended_cleanly:
            if stop_event.is_set():
                emit({'event': 'stopped', 'count': count, 'reason': 'hotkey'})

    except KeyboardInterrupt:
        emit({'event': 'stopped', 'count': count, 'reason': 'signal'})
    except Exception as e:
        emit({
            'event': 'error',
            'count': count,
            'message': str(e),
            'trace': traceback.format_exc()
        })


if __name__ == '__main__':
    try:
        if len(sys.argv) < 2:
            emit({'event': 'error', 'message': 'Missing config path or command.'})
            sys.exit(0)

        arg = sys.argv[1]

        if arg == '--get-position':
            get_position()
            sys.exit(0)

        try:
            with open(arg, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            emit({'event': 'error', 'message': 'Could not read config: ' + str(e)})
            sys.exit(0)

        run_clicker(config)
    except Exception as e:
        emit({'event': 'error', 'message': 'Fatal: ' + str(e), 'trace': traceback.format_exc()})

    # Always exit 0 — we report errors via JSON events, not exit codes, so the
    # Electron parent can display a meaningful message instead of "exit code 1".
    sys.exit(0)
