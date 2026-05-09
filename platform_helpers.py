# Copyright (c) 2026 Myles Willett. All rights reserved.
# Proprietary and confidential. No reproduction, distribution, or use
# without express written permission.

"""
WillettBot Platform Helpers — Dispatcher
========================================
Single entry point for every platform-specific call in WillettBot. Detects the
host OS at import time and delegates to the matching backend:

    macOS   → platform_mac.py    (osascript / AppleScript / AXIsProcessTrusted)
    Windows → platform_win.py    (pywin32 / Shell.Application COM)
    Linux   → platform_linux.py  (xdotool / wmctrl / xdg-open)

Every backend exports the SAME public names with the SAME signatures, so
recorder.py / runner.py / permcheck.py / clicker.py never need to branch on
sys.platform — they just call platform_helpers.get_frontmost_app() and the
right code runs underneath.

Public API
----------
Queries (no side effects):
    get_frontmost_app(timeout=2.0)            -> str
    get_frontmost_window_title(timeout=2.0)   -> str
    get_frontmost_window_rect(timeout=2.0)    -> {x,y,w,h} | None
    get_window_count(app, timeout=2.0)        -> int | None
    get_file_manager_name()                   -> str   # "Finder" | "Explorer" | "Files"
    get_file_manager_front_path(timeout=1.5)  -> str
    get_file_manager_selection(timeout=1.5)   -> str

App / window control:
    activate_app(app, timeout=2.0)                       -> bool
    raise_window_by_title(app, title, timeout=2.5)       -> bool
    focus_modal_dialog(app, timeout=2.5)                 -> bool
    wait_until_frontmost(app, timeout=1.2)               -> bool
    open_app(name, timeout=5.0)                          -> None
    open_file(path, app='', timeout=10.0)                -> tuple[bool, str]
    open_directory_in_place(path, timeout=5.0)           -> bool

Permission probes:
    check_accessibility()        -> bool | None
    check_automation()           -> bool | None
    probe_automation_permission() -> dict

Native scripting:
    run_native_script(script, timeout=30.0) -> str   # raises on failure

Constants:
    PLATFORM_NAME            'macOS' | 'Windows' | 'Linux'
    NATIVE_SCRIPT_ACTION     'applescript' | 'powershell' | 'shell'
    NEEDS_ACCESSIBILITY_GRANT  bool   # macOS only — controls warning paths

Usage:
    from platform_helpers import get_frontmost_app, PLATFORM_NAME
    app = get_frontmost_app()
"""

import sys

# Detect platform once at import time. We re-export every public symbol from
# the matching backend so callers can `from platform_helpers import X` without
# caring which OS they're on.
if sys.platform == 'darwin':
    from platform_mac import *           # noqa: F401, F403
    from platform_mac import (            # noqa: F401  (explicit for IDEs)
        get_frontmost_app,
        get_frontmost_window_title,
        get_frontmost_window_rect,
        get_window_count,
        get_file_manager_name,
        get_file_manager_front_path,
        get_file_manager_selection,
        activate_app,
        raise_window_by_title,
        focus_modal_dialog,
        wait_until_frontmost,
        open_app,
        open_file,
        open_directory_in_place,
        check_accessibility,
        check_automation,
        probe_automation_permission,
        run_native_script,
        get_real_modifier_state,
        PLATFORM_NAME,
        NATIVE_SCRIPT_ACTION,
        NEEDS_ACCESSIBILITY_GRANT,
    )
elif sys.platform == 'win32':
    from platform_win import *           # noqa: F401, F403
    from platform_win import (            # noqa: F401
        get_frontmost_app,
        get_frontmost_window_title,
        get_frontmost_window_rect,
        get_window_count,
        get_file_manager_name,
        get_file_manager_front_path,
        get_file_manager_selection,
        activate_app,
        raise_window_by_title,
        focus_modal_dialog,
        wait_until_frontmost,
        open_app,
        open_file,
        open_directory_in_place,
        check_accessibility,
        check_automation,
        probe_automation_permission,
        run_native_script,
        get_real_modifier_state,
        PLATFORM_NAME,
        NATIVE_SCRIPT_ACTION,
        NEEDS_ACCESSIBILITY_GRANT,
    )
else:
    # Linux + anything else (BSD, etc) — falls through to the X11/xdotool
    # backend. If the user is on Wayland, most of these will no-op gracefully
    # and queries will return ''. We still ship the file so the import doesn't
    # explode and the rest of WillettBot can run (recording will work via
    # pynput, replay via pyautogui, just no app-context detection).
    from platform_linux import *         # noqa: F401, F403
    from platform_linux import (          # noqa: F401
        get_frontmost_app,
        get_frontmost_window_title,
        get_frontmost_window_rect,
        get_window_count,
        get_file_manager_name,
        get_file_manager_front_path,
        get_file_manager_selection,
        activate_app,
        raise_window_by_title,
        focus_modal_dialog,
        wait_until_frontmost,
        open_app,
        open_file,
        open_directory_in_place,
        check_accessibility,
        check_automation,
        probe_automation_permission,
        run_native_script,
        get_real_modifier_state,
        PLATFORM_NAME,
        NATIVE_SCRIPT_ACTION,
        NEEDS_ACCESSIBILITY_GRANT,
    )


def is_mac():
    return sys.platform == 'darwin'


def is_windows():
    return sys.platform == 'win32'


def is_linux():
    return sys.platform.startswith('linux')


# ── SELF-TEST ───────────────────────────────────────────────────────────────
# Run as `python platform_helpers.py` to smoke-test the platform backend on
# whatever Python interpreter you've got. Prints clear diagnostics — including
# a "pywin32 not installed" hint on Windows — instead of just returning ''
# from queries and leaving you to guess. Used in WINDOWS-SETUP.md step 5.
def _self_test():
    print('PLATFORM_NAME:        ' + str(PLATFORM_NAME))
    print('NATIVE_SCRIPT_ACTION: ' + str(NATIVE_SCRIPT_ACTION))
    print('python:               ' + sys.executable)
    print('---')
    front = get_frontmost_app()
    print('frontmost app:        ' + (front if front else '(empty)'))
    title = get_frontmost_window_title()
    print('frontmost title:      ' + (title if title else '(empty)'))
    print('file manager name:    ' + str(get_file_manager_name()))
    fm_path = get_file_manager_front_path()
    print('file manager path:    ' + (fm_path if fm_path else '(empty)'))
    # Diagnostic when queries returned blank — point the user at the most
    # likely cause per platform so they don't have to guess.
    if not front:
        print('---')
        if is_windows():
            try:
                from platform_win import _w32_import_error
            except Exception:
                _w32_import_error = None
            if _w32_import_error:
                print('DIAGNOSTIC: pywin32 is not installed in THIS Python.')
                print('            Import error: ' + str(_w32_import_error))
                print('')
                print('  This Python:  ' + sys.executable)
                print('')
                print('  Fix (choose one):')
                print('   1. Install pywin32 in this Python:')
                print('        py -m pip install pywin32')
                print('   2. Or just use the app\'s venv Python — it already')
                print('      has pywin32 because the app installed it on first')
                print('      launch. The venv lives at:')
                print('        %APPDATA%\\WillettBot\\venv\\Scripts\\python.exe')
                print('')
                print('  The app itself uses the venv Python, so a blank result')
                print('  here does NOT mean the app is broken — it just means')
                print('  THIS Python (your shell\'s python) is missing pywin32.')
            else:
                print('DIAGNOSTIC: pywin32 imported but the foreground-window')
                print('            query returned nothing. The foreground may be')
                print('            the desktop or a UAC-protected window. Click')
                print('            on a normal app window and try again.')
        elif is_mac():
            print('DIAGNOSTIC: empty frontmost app on Mac usually means')
            print('            Automation permission was denied. Check:')
            print('              tccutil reset AppleEvents com.willett.willettbot')
        else:
            print('DIAGNOSTIC: empty frontmost app on Linux usually means')
            print('            xdotool/wmctrl is not installed, or you are on')
            print('            Wayland (which blocks X11 introspection). Try:')
            print('              sudo apt install xdotool wmctrl')


if __name__ == '__main__':
    _self_test()
