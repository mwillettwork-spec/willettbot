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
        PLATFORM_NAME,
        NATIVE_SCRIPT_ACTION,
        NEEDS_ACCESSIBILITY_GRANT,
    )
elif sys.platform == 'win32':
    from platform_win import *           # noqa: F401, F403
    from platform_win import (            # noqa: F401
        get_frontmost_app,
        get_frontmost_window_title,
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
