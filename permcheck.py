"""
WillettBot Permission Self-Check
================================
macOS tracks Accessibility / Automation / Input Monitoring permissions PER
binary (by code signature, or by SHA hash for unsigned binaries). Our bundled
Python is a separate unsigned binary from the Electron shell — so even if the
user has already granted those permissions to WillettBot.app, the bundled
Python needs its own grants for pyautogui clicks, pynput hotkeys, and osascript
control of Finder / System Events to actually work.

Without this check, those denials are INVISIBLE:
  - pyautogui.click() silently drops the event (no exception)
  - pynput.Listener starts but receives no events
  - osascript returns error -1743 which gets swallowed

This script probes each permission and returns a JSON dict so the Electron
parent can show a setup banner. It's intentionally side-effect-free — no
clicks, no keystrokes, no lingering listeners.

Output format (one JSON line on stdout):
  {
    "accessibility":    true | false | null,   # pyautogui clicks
    "automation":       true | false | null,   # osascript → System Events
    "input_monitoring": true | false | null,   # pynput hotkey listener
    "python_path":      "/full/path/to/this/python3"
  }

`null` means we couldn't determine — treated as "probably ok" by the UI so
we don't pester the user on a weird config.
"""
import json
import sys
import ctypes
import ctypes.util
import subprocess
import threading
import time


def check_accessibility():
    """AXIsProcessTrusted() — returns true iff this process can synthesize
    mouse / keyboard events via the Quartz event tap (what pyautogui uses).
    No prompt shown, no side effects.

    IMPORTANT: on unsigned / ad-hoc-signed binaries (which is everything we
    ship until we get a Developer ID), this returns FALSE even when the
    permission is granted and clicks actually work. It's a code-signature
    vs. TCC-hash mismatch thing we can't fix client-side. So we:
      - Return True if the call succeeds (that result is reliable).
      - Return None (inconclusive) otherwise. Upstream UI treats None as
        'probably ok' and won't cry wolf.
    If the grant is genuinely missing the user sees clicks silently no-op
    at runtime, and the 'Clicks not working? Fix permissions' link is
    always one tap away."""
    try:
        lib = ctypes.CDLL(ctypes.util.find_library('ApplicationServices'))
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        if bool(lib.AXIsProcessTrusted()):
            return True
        return None  # don't claim denied — AXIsProcessTrusted false-negatives
    except Exception:
        return None


def check_automation():
    """Harmless osascript call to System Events. If macOS rejects with -1743
    (errAEEventNotPermitted), Automation permission is denied. Anything else
    (success OR unrelated error) is treated as 'probably ok'."""
    try:
        r = subprocess.run(
            ['osascript', '-e',
             'tell application "System Events" to get name of first '
             'application process whose frontmost is true'],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0:
            return True
        stderr_low = (r.stderr or '').lower()
        if ('-1743' in stderr_low
                or 'not authorized to send apple events' in stderr_low
                or 'not allowed assistive access' in stderr_low):
            return False
        return None  # Unrelated error — don't claim denied
    except Exception:
        return None


def check_input_monitoring():
    """Start a pynput keyboard listener briefly. On macOS, without Input
    Monitoring the listener either raises during init or the NSEvent tap
    silently never fires (but the listener's .running flag is still True).
    We can at least catch the init-error case. For the silent-fail case we
    return None (inconclusive) rather than lying.

    Importing pynput is cheap; starting a listener briefly is the only way
    to probe the tap."""
    try:
        from pynput import keyboard as _kb
    except Exception:
        return None

    started = threading.Event()
    errored = [None]

    def _target():
        try:
            listener = _kb.Listener(on_press=lambda k: None)
            listener.start()
            started.set()
            # Give it a moment — if the Cocoa event tap rejects us, pynput
            # typically logs to stderr but doesn't raise. We can't detect
            # that from here, so we just stop and report inconclusive.
            time.sleep(0.15)
            try:
                listener.stop()
            except Exception:
                pass
        except Exception as e:
            errored[0] = str(e)
            started.set()

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    started.wait(timeout=2.0)
    if errored[0]:
        return False
    # Inconclusive — start succeeded but we can't tell if taps will fire.
    # Treat as "probably ok"; downstream UI doesn't raise a warning on null.
    return None


def main():
    out = {
        'accessibility':    check_accessibility(),
        'automation':       check_automation(),
        'input_monitoring': check_input_monitoring(),
        'python_path':      sys.executable,
    }
    print(json.dumps(out), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(json.dumps({'error': str(e)}), flush=True)
    sys.exit(0)
