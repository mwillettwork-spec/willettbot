# Copyright (c) 2026 Myles Willett. All rights reserved.
# Proprietary and confidential. No reproduction, distribution, or use
# without express written permission.

"""
WillettBot Permission Self-Check
================================
macOS tracks Accessibility / Automation / Input Monitoring permissions PER
binary (by code signature, or by SHA hash for unsigned binaries). Our bundled
Python is a separate unsigned binary from the Electron shell — so even if the
user has already granted those permissions to WillettBot.app, the bundled
Python needs its own grants for pyautogui clicks, pynput hotkeys, and
osascript control of Finder / System Events to actually work.

Without this check, those denials are INVISIBLE on macOS:
  - pyautogui.click() silently drops the event (no exception)
  - pynput.Listener starts but receives no events
  - osascript returns error -1743 which gets swallowed

This script probes each permission and returns a JSON dict so the Electron
parent can show a setup banner. It's intentionally side-effect-free — no
clicks, no keystrokes, no lingering listeners.

On Windows / Linux there's no equivalent permission gate, so the probes
return True (or null when we can't tell) and the setup banner stays hidden.

Output format (one JSON line on stdout):
  {
    "accessibility":    true | false | null,   # input simulation
    "automation":       true | false | null,   # OS automation backend
    "input_monitoring": true | false | null,   # pynput hotkey listener
    "python_path":      "/full/path/to/this/python3",
    "platform":         "macOS" | "Windows" | "Linux"
  }

`null` means we couldn't determine — treated as "probably ok" by the UI so
we don't pester the user on a weird config.
"""
import json
import sys
import threading
import time

import platform_helpers as platform


def check_accessibility():
    """True iff this binary is allowed to post mouse / keyboard events. On
    macOS this calls AXIsProcessTrusted (which can false-negative on unsigned
    binaries — we handle that by returning None instead of False, so the UI
    doesn't cry wolf when clicks actually work). On Windows / Linux there's
    no equivalent gate so this returns True."""
    return platform.check_accessibility()


def check_automation():
    """True iff this binary can drive other apps (System Events on Mac,
    pywin32 on Windows, xdotool/wmctrl on Linux). False on a confirmed
    macOS Automation denial; True on Win/Linux; None on other failures."""
    return platform.check_automation()


def check_input_monitoring():
    """Start a pynput keyboard listener briefly to see if it errors at init.
    On macOS, without Input Monitoring the listener either raises during
    init or the NSEvent tap silently never fires (but the .running flag is
    still True). We can at least catch the init-error case. For the silent-
    fail case we return None (inconclusive) rather than lying.

    On Windows / Linux pynput just works — listener init shouldn't raise."""
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
            # Give it a moment — if the platform's event tap rejects us,
            # pynput typically logs to stderr but doesn't raise. We can't
            # detect that from here, so we just stop and report inconclusive.
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
        'platform':         platform.PLATFORM_NAME,
    }
    print(json.dumps(out), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(json.dumps({'error': str(e)}), flush=True)
    sys.exit(0)
