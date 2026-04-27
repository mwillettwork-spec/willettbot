# Copyright (c) 2026 Myles Willett. All rights reserved.
# Proprietary and confidential. No reproduction, distribution, or use
# without express written permission.

"""
WillettBot Script Recorder
==========================
Captures mouse clicks and keyboard input while the user performs an action,
then compiles those events into a runner.py-compatible JSON script.

Run as:
    python3 recorder.py --start-hotkey f9 --end-hotkey f10 --name "My Script"

The recorder sits idle until the user presses the start hotkey, then watches
global mouse clicks + keystrokes until the user presses the end hotkey. The
start/end hotkeys themselves are NOT part of the recording.

Event protocol (one JSON object per line on stdout):
    {"event": "ready",    "startHotkey": "f9", "endHotkey": "f10"}
    {"event": "started"}
    {"event": "captured", "action": {...}}       # live feedback per event
    {"event": "done",     "script": {...}}       # final compiled script
    {"event": "error",    "message": "..."}
"""

import sys
import json
import time
import argparse
import threading
import traceback
import signal

# Cross-platform helpers — auto-dispatches to platform_mac / platform_win /
# platform_linux based on sys.platform. All osascript / pywin32 / xdotool
# calls live behind this single import.
import platform_helpers as platform


# AUTOMATION-PERMISSION STARTUP PROBE ────────────────────────────────────────
# Runs ONCE before listeners start. On macOS this triggers the System Events
# Automation prompt synchronously so we get a clear "ok / denied / silent /
# timeout / error" signal instead of silent failure in the background ctx
# thread. On Windows / Linux this is essentially a smoke test that the
# platform backend (pywin32 / xdotool) is alive and queries return data.
def probe_automation_permission():
    """Returns {status, returncode, stdout, stderr, osascript} where status
    is one of 'ok' | 'denied' | 'silent' | 'timeout' | 'error'. The
    'osascript' field name is kept for protocol compatibility with the hub —
    on Windows / Linux it just means 'platform automation backend reachable'."""
    return platform.probe_automation_permission()

try:
    from pynput import keyboard, mouse
except ImportError as e:
    print(json.dumps({
        "event": "error",
        "message": "pynput import failed: " + str(e) +
                   ". Install it with:  pip3 install --user pynput"
    }), flush=True)
    sys.exit(0)


# ── EVENT EMITTER ───────────────────────────────────────────────────────────

def emit(evt):
    """Print one JSON event line on stdout for the Electron parent."""
    try:
        print(json.dumps(evt), flush=True)
    except Exception:
        pass


# ── KEY NAME TABLES ─────────────────────────────────────────────────────────
# pynput exposes non-printable keys as Key enum values. We translate them to
# pyautogui-compatible names so runner.py can replay them directly.

SPECIAL_KEY_NAMES = {
    keyboard.Key.enter: 'enter',
    keyboard.Key.tab: 'tab',
    keyboard.Key.esc: 'esc',
    keyboard.Key.space: 'space',
    keyboard.Key.backspace: 'backspace',
    keyboard.Key.delete: 'delete',
    keyboard.Key.up: 'up',
    keyboard.Key.down: 'down',
    keyboard.Key.left: 'left',
    keyboard.Key.right: 'right',
    keyboard.Key.home: 'home',
    keyboard.Key.end: 'end',
    keyboard.Key.page_up: 'pageup',
    keyboard.Key.page_down: 'pagedown',
}
for _i in range(1, 13):
    _k = getattr(keyboard.Key, 'f' + str(_i), None)
    if _k is not None:
        SPECIAL_KEY_NAMES[_k] = 'f' + str(_i)

MODIFIER_KEYS = {
    keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r,
    keyboard.Key.ctrl,  keyboard.Key.ctrl_l,  keyboard.Key.ctrl_r,
    keyboard.Key.cmd,   keyboard.Key.cmd_l,   keyboard.Key.cmd_r,
    keyboard.Key.alt,   keyboard.Key.alt_l,   keyboard.Key.alt_r,
}


def modifier_name(key):
    if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r): return 'shift'
    if key in (keyboard.Key.ctrl,  keyboard.Key.ctrl_l,  keyboard.Key.ctrl_r):  return 'ctrl'
    if key in (keyboard.Key.cmd,   keyboard.Key.cmd_l,   keyboard.Key.cmd_r):   return 'command'
    if key in (keyboard.Key.alt,   keyboard.Key.alt_l,   keyboard.Key.alt_r):   return 'alt'
    return None


def _modified_key_name(key):
    """Resolve a non-modifier key event to a single combo-name letter/digit
    (e.g. 'c' for the C key, '7' for the 7 key) — robust to a Windows pynput
    quirk where key.char comes back as a control character when Ctrl is held
    (\\x03 for Ctrl+C, \\x16 for Ctrl+V, etc.) and the literal char is wrong.

    Strategy:
      1. Prefer key.vk (Win32 virtual key code on Windows; HID/macOS/X11
         scancode-mapped on the others). For ASCII letters and digits, the
         VK codes happen to align with ASCII: 0x30-0x39 = '0'-'9',
         0x41-0x5A = 'A'-'Z'. We lowercase letters because runner.py /
         pyautogui expect lowercase combo names ('ctrl', 'c').
      2. Fall back to key.char. If that came through as a control character
         (\\x01-\\x1A), translate it back to its source letter.
      3. Final fallback: special-key table (function keys, arrows, etc.).
    Returns None if we genuinely can't make sense of the key — caller skips."""
    vk = getattr(key, 'vk', None)
    if isinstance(vk, int):
        if 0x30 <= vk <= 0x39:                # digits 0-9
            return chr(vk)
        if 0x41 <= vk <= 0x5A:                # letters A-Z (return lowercase)
            return chr(vk + 0x20)
    # Try the character. May be None (no .char attr), a printable letter, or
    # a control character on Windows when modifiers are active.
    try:
        char = key.char
    except AttributeError:
        char = None
    if char and len(char) == 1:
        o = ord(char)
        if 1 <= o <= 26:                       # \x01-\x1A → 'a'-'z'
            return chr(o + ord('a') - 1)
        if char.isalnum():
            return char.lower()
    # Special key (F1, arrows, etc.) — use the existing table.
    return SPECIAL_KEY_NAMES.get(key)


def hotkey_matches(key, hotkey_name):
    """Does this key event match the single-key hotkey (e.g. 'f9', 'esc',
    'return', 'shift')? We normalize a few friendly aliases to their pynput
    attribute names — pynput calls Return 'enter', for example."""
    # Friendly-name → pynput attribute-name aliases. Lets the UI offer
    # non-Fn fallbacks for users whose function keys are media-key by default
    # (e.g. MacBook keyboards without Fn lock).
    alias = {
        'return': 'enter',     # pynput exposes Return as Key.enter
        'escape': 'esc',
    }
    name = alias.get(hotkey_name, hotkey_name)
    target = getattr(keyboard.Key, name, None)
    if target is None:
        return False
    return key == target


# ── RECORDER ────────────────────────────────────────────────────────────────

# Tuning constants. Kept at module scope so they're easy to tweak later.
MOVE_SAMPLE_MIN_INTERVAL = 0.22   # seconds — don't sample moves faster than this
MOVE_SAMPLE_MIN_DISTANCE = 18     # pixels — require this much delta to record a move
DOUBLE_CLICK_THRESHOLD   = 0.5    # seconds — two clicks within this merge into double_click
DOUBLE_CLICK_RADIUS      = 6      # pixels — click positions must be within this radius to merge
DRAG_MIN_DISTANCE        = 5      # pixels — mouse-down → mouse-up further than this is a drag
MIN_WAIT                 = 0.15   # seconds — gaps >= this become explicit wait actions
                                  # Tightened from 0.4 → 0.15 so replay timing
                                  # tracks user-input timing more closely.
                                  # pyautogui.PAUSE in runner.py is 0.05s,
                                  # so wait actions below ~0.15s mostly get
                                  # absorbed by that floor anyway.
MOVE_DURATION_MIN        = 0.08   # seconds — minimum glide time so playback doesn't teleport
MOVE_DURATION_MAX        = 1.5    # seconds — cap any single glide so long pauses don't stall

# Context-polling (app switches + Finder navigation). Lets us replace raw
# pixel clicks on dock icons / Finder folders with semantic focus_app /
# open_file actions that actually work on replay.
#
# The poll interval is 0.2s — tighter than you might expect because missing
# a Finder→Preview transition by 0.2s means the click never gets upgraded to
# an open_file, and the script replays as a raw double_click at stored
# coordinates (fragile as soon as window position changes). We also wake the
# poller immediately after every click, plus once more ~0.35s later, to
# catch app-launches that lag the click.
CONTEXT_POLL_INTERVAL    = 0.2    # seconds — how often to query macOS
CONTEXT_POST_CLICK_DELAY = 0.35   # seconds — schedule a second wake this far
                                  #           after a click to catch slow app
                                  #           launches (Preview, PDF viewers, etc.)
CONTEXT_COALESCE_WINDOW  = 2.0    # seconds — a context event within this window
                                  #           of a click/hotkey REPLACES that click
                                  #           (rather than appending a new step)

# Horizontal-scroll → switch_desktop detection. 3-finger trackpad swipes for
# Mission Control are consumed by WindowServer before pynput sees them, but
# 2-finger horizontal scrolls DO come through as scroll events with nonzero
# dx. We accumulate |dx| across a short time window; a burst over threshold
# becomes one switch_desktop step (direction = sign of the summed dx).
HSCROLL_BURST_WINDOW     = 0.35   # seconds — flush + emit when no scroll for this long
HSCROLL_BURST_THRESHOLD  = 3      # |sum dx| must exceed this to count as a gesture


class Recorder:
    def __init__(self, start_hotkey, end_hotkey):
        self.start_hotkey = start_hotkey
        self.end_hotkey = end_hotkey
        self.state = 'waiting'              # 'waiting' → 'recording' → 'done'
        self.events = []                    # list of (ts, action_dict)
        self._events_lock = threading.Lock()
        self.active_modifiers = set()
        self.done_event = threading.Event()
        self.start_time = None

        # Mouse tracking: we downsample moves and defer clicks until release
        # so we can tell apart click/double-click/drag.
        self._last_move_pos = None
        self._last_move_ts = 0.0
        self._pending_press = None          # {x, y, button, ts}

        # Context-polling state (mutated by the poll thread).
        self._ctx_prev_app = None           # last-seen frontmost app name
        self._ctx_prev_window_title = None  # last-seen frontmost window title
                                            # (used to refocus THE right window
                                            # on replay, not just the right app)
        self._ctx_prev_finder_path = None   # last-seen Finder window path
        self._ctx_prev_finder_sel = None    # last-seen Finder selection path
        self._ctx_prev_window_count = {}    # app-name → last-seen window count
                                            # (drops → user closed a window)
        self._ctx_window_drop_pending = {}  # app-name → consecutive polls
                                            # we've seen the count BELOW the
                                            # baseline. Required to be ≥3
                                            # before we commit a close event,
                                            # so transient flickers (tooltips,
                                            # modal dismiss animations, rapid
                                            # finder navigation) don't fire
                                            # bogus Cmd+W / Ctrl+W hotkeys.
        self._ctx_last_fm_nav_ts = 0.0      # timestamp of the most recent
                                            # __ctx_finder_nav__ emission. Used
                                            # to freeze close-detection on the
                                            # file manager during rapid folder
                                            # drilling — Finder/Explorer's
                                            # window count can briefly look
                                            # like it dropped to 0 during the
                                            # transition between two folders,
                                            # and that was producing phantom
                                            # Cmd+W events in the wild.

        # Horizontal-scroll accumulator for switch_desktop detection.
        self._hscroll_sum = 0.0             # signed dx sum over the active burst
        self._hscroll_last_ts = 0.0         # ts of most recent h-scroll event

        # Wake signal for the context poller. on_click sets this so the
        # poller re-samples immediately instead of sleeping out its 0.2s
        # interval — closes the race where an app-launch triggered by the
        # click would otherwise be missed on the next poll tick.
        self._ctx_wake = threading.Event()

    # ── keyboard ─────────────────────────────────────────────────────────
    def on_key_press(self, key):
        # Waiting for the user to press start — ignore everything else.
        if self.state == 'waiting':
            if hotkey_matches(key, self.start_hotkey):
                self.state = 'recording'
                self.start_time = time.time()
                emit({"event": "started"})
            return

        if self.state != 'recording':
            return

        # End hotkey wins over everything else. Don't record it.
        if hotkey_matches(key, self.end_hotkey):
            self.state = 'done'
            self.done_event.set()
            return False   # stops the keyboard listener

        now = time.time()

        # Track modifier state but don't emit anything yet.
        if key in MODIFIER_KEYS:
            mn = modifier_name(key)
            if mn:
                self.active_modifiers.add(mn)
            return

        # Sanity-check active_modifiers against the OS's real key state. On
        # Windows this catches the false-positive-hotkey bug where pynput
        # missed a Ctrl-release event (because focus changed, a modal stole
        # it, etc.) and 'ctrl' is left stuck in active_modifiers — which
        # would otherwise turn the very next plain-letter keypress into a
        # bogus Ctrl+letter hotkey. On Mac/Linux the platform helper returns
        # None, meaning "trust active_modifiers as-is" (pynput is reliable
        # there). Any modifier the OS says ISN'T held gets discarded.
        try:
            real = platform.get_real_modifier_state()
        except Exception:
            real = None
        if real is not None:
            stale = self.active_modifiers - real
            if stale:
                self.active_modifiers -= stale

        # Is a non-shift modifier active? Then this is a hotkey combo.
        mods = self.active_modifiers - {'shift'}

        try:
            char = key.char
        except AttributeError:
            char = None

        if mods:
            keys_list = sorted(mods)
            # Use vk-first resolution so Ctrl+C / Ctrl+V record correctly on
            # Windows (where pynput reports key.char as a control character
            # when Ctrl is held — see _modified_key_name docstring).
            combo = _modified_key_name(key)
            # Debug-emit on the FIRST hotkey of a recording so we can see what
            # pynput actually delivered if a user reports a wrong combo. Goes
            # to stdout as a regular event line; the hub log shows it.
            if not getattr(self, '_dbg_first_hotkey_emitted', False):
                try:
                    raw_vk   = getattr(key, 'vk', None)
                    raw_char = None
                    try: raw_char = key.char
                    except AttributeError: pass
                    emit({"event": "log",
                          "message": "[recorder dbg] first hotkey: vk=" + str(raw_vk) +
                                     " char=" + repr(raw_char) +
                                     " resolved=" + repr(combo) +
                                     " mods=" + repr(sorted(mods))})
                except Exception:
                    pass
                self._dbg_first_hotkey_emitted = True
            if not combo:
                return
            keys_list.append(combo)
            self._record(now, {"action": "hotkey", "keys": keys_list})
            return

        if char is not None:
            # A printable character. Stored as a 'char' event; compile()
            # coalesces consecutive chars into a single "type" action.
            self._record(now, {"action": "char", "char": char})
            return

        sk = SPECIAL_KEY_NAMES.get(key)
        if sk:
            self._record(now, {"action": "press", "key": sk})

    def on_key_release(self, key):
        if key in MODIFIER_KEYS:
            mn = modifier_name(key)
            if mn:
                self.active_modifiers.discard(mn)

    # ── mouse: movement sampling ─────────────────────────────────────────
    def on_move(self, x, y):
        if self.state != 'recording':
            return
        # If a mouse button is currently held, don't record intermediate
        # positions — they'll be folded into the drag we emit on release.
        if self._pending_press is not None:
            return
        now = time.time()
        if self._last_move_pos is not None:
            if (now - self._last_move_ts) < MOVE_SAMPLE_MIN_INTERVAL:
                return
            dx = abs(int(x) - self._last_move_pos[0])
            dy = abs(int(y) - self._last_move_pos[1])
            if dx < MOVE_SAMPLE_MIN_DISTANCE and dy < MOVE_SAMPLE_MIN_DISTANCE:
                return
        self._last_move_pos = (int(x), int(y))
        self._last_move_ts = now
        # Duration is filled in during compile so it matches real-time pacing.
        self._record(now, {"action": "move_to", "x": int(x), "y": int(y)})

    # ── mouse: horizontal scroll → switch_desktop ────────────────────────
    # Best-effort only: macOS suppresses 3-finger desktop-switch swipes at
    # the WindowServer level so pynput never sees them. Two-finger horizontal
    # scrolls DO make it through, and users can also add switch_desktop
    # blocks manually via the editor.
    def on_scroll(self, x, y, dx, dy):
        if self.state != 'recording':
            return
        # Ignore vertical scrolls — we only care about horizontal bursts.
        if not dx:
            return
        now = time.time()
        # If the previous burst ended long ago, reset.
        if (now - self._hscroll_last_ts) > HSCROLL_BURST_WINDOW and self._hscroll_sum != 0:
            self._flush_hscroll()
        self._hscroll_sum += float(dx)
        self._hscroll_last_ts = now

    def _flush_hscroll(self):
        """Called when an h-scroll burst ends (either explicitly by a non-scroll
        event or by the poll thread noticing idle time). Emits a
        switch_desktop step if the burst was large enough."""
        s = self._hscroll_sum
        self._hscroll_sum = 0.0
        if abs(s) < HSCROLL_BURST_THRESHOLD:
            return
        # macOS + pynput: positive dx = scroll right (finger swiped left →
        # content moved right); that corresponds to "move one space left"
        # (Ctrl+Left) in Mission Control. Invert so user-intent is preserved.
        direction = 'left' if s > 0 else 'right'
        self._record(time.time(), {
            "action": "switch_desktop",
            "direction": direction,
            "count": 1
        })

    def _attach_app(self, action):
        """If the poll thread has cached a frontmost app, tag this action with
        it so the runner can refocus on replay if the app isn't already
        frontmost. Avoids the 'click lands on the wrong app because windows
        moved' problem without requiring pixel-perfect window geometry.

        Also tags the frontmost window title when we have one: that lets the
        runner do a smarter refocus (raise the *specific* window the user was
        clicking into rather than whatever window of that app is frontmost),
        which is the cheapest pixel-coord-reduction win we can ship today.
        """
        app = self._ctx_prev_app
        if app:
            action['app'] = app
        title = self._ctx_prev_window_title
        if title:
            action['window_title'] = title
        return action

    # ── mouse: clicks + drags ────────────────────────────────────────────
    def on_click(self, x, y, button, pressed):
        if self.state != 'recording':
            return
        now = time.time()
        btn_name = 'left'
        if button == mouse.Button.right:
            btn_name = 'right'
        elif button == mouse.Button.middle:
            btn_name = 'middle'

        if pressed:
            # Defer recording until release so we can tell click vs drag apart.
            self._pending_press = {
                'x': int(x), 'y': int(y),
                'button': btn_name, 'ts': now
            }
            return

        press = self._pending_press
        self._pending_press = None
        if not press or press['button'] != btn_name:
            return

        px, py = press['x'], press['y']
        dx = abs(int(x) - px)
        dy = abs(int(y) - py)

        # ── DRAG: button was held while mouse moved meaningfully ─────────
        if dx > DRAG_MIN_DISTANCE or dy > DRAG_MIN_DISTANCE:
            self._record(now, self._attach_app({
                "action": "drag",
                "fromX": px, "fromY": py,
                "toX": int(x), "toY": int(y),
                "button": btn_name,
                "duration": round(max(0.2, now - press['ts']), 2)
            }))
            # Reset move baseline so the next sample isn't spuriously close.
            self._last_move_pos = (int(x), int(y))
            self._last_move_ts = now
            return

        # ── DOUBLE-CLICK: prior event was a click at ~same spot within threshold ─
        # We mutate the prior click in-place so the script only has one action.
        if self.events:
            last_ts, last_action = self.events[-1]
            if (last_action.get('action') == 'click' and
                    last_action.get('button') == btn_name and
                    abs(last_action.get('x', 0) - px) <= DOUBLE_CLICK_RADIUS and
                    abs(last_action.get('y', 0) - py) <= DOUBLE_CLICK_RADIUS and
                    (press['ts'] - last_ts) <= DOUBLE_CLICK_THRESHOLD):
                last_action['action'] = 'double_click'
                emit({
                    "event": "captured",
                    "action": last_action,
                    "replacesPrevious": True
                })
                return

        # ── NORMAL CLICK ─────────────────────────────────────────────────
        self._record(press['ts'], self._attach_app({
            "action": "click",
            "x": px, "y": py,
            "button": btn_name
        }))
        # Nudge the context poller to re-sample right now — catches
        # Finder→Preview / Dock→app / folder-navigate transitions that
        # happen within the 0.2s poll gap. A second nudge fires ~0.35s
        # later to catch slow-launching apps.
        self._kick_ctx_poll()

    def _kick_ctx_poll(self):
        """Signal the poll loop to re-sample immediately and schedule one
        follow-up wake to cover apps that take a moment to launch after
        their click was dispatched.

        Also kicks off a synchronous-ish Finder-selection probe if the most
        recent poll saw Finder frontmost. The poll thread only refreshes
        `_ctx_prev_finder_sel` while Finder is active, so if a user clicks
        fast enough that the subsequent poll finds Preview (not Finder)
        frontmost, we'd lose the selection anchor and the file-open marker
        would never fire. This background probe captures the selection
        immediately, in parallel with whatever app-launch is happening —
        whichever query returns first wins."""
        self._ctx_wake.set()

        def _delayed_wake():
            time.sleep(CONTEXT_POST_CLICK_DELAY)
            if not self.done_event.is_set():
                self._ctx_wake.set()
        threading.Thread(target=_delayed_wake, daemon=True).start()

        # If the last observation was the file manager (Finder / Explorer /
        # Files / etc.), snapshot selection NOW in the background — the
        # underlying query takes ~50ms, but so does app-launching, and we
        # want whichever happens first to be this query. Guarded by the
        # cached prev_app so we don't spam the OS on every click.
        if self._ctx_prev_app == platform.get_file_manager_name():
            def _probe_selection():
                try:
                    sel = self._query_finder_selection()
                except Exception:
                    sel = ''
                if sel:
                    self._ctx_prev_finder_sel = sel
                # Also re-wake the poller once our probe returns, so the
                # app-switch check runs with the freshest selection cached.
                if not self.done_event.is_set():
                    self._ctx_wake.set()
            threading.Thread(target=_probe_selection, daemon=True).start()

    # ── internal ─────────────────────────────────────────────────────────
    def _record(self, ts, action):
        with self._events_lock:
            self.events.append((ts, action))
        emit({"event": "captured", "action": action})

    # ── CONTEXT-POLLING QUERIES ──────────────────────────────────────────
    # These thin wrappers delegate to platform_helpers, which dispatches to
    # the right OS backend (osascript on Mac, pywin32 on Win, xdotool on
    # Linux). The wrappers exist to centralize the one-shot permission-denied
    # warning emission for the macOS Automation prompt — on Win/Linux that
    # gate doesn't exist and the warning never fires.

    # Class-level flag: once we've emitted the "permission denied" warning we
    # don't spam the user on every subsequent poll. Reset per-Recorder instance.
    _perm_warning_emitted = False

    @classmethod
    def _maybe_warn_about_permissions(cls):
        """Emit a one-shot warning if the platform's automation backend looks
        denied. Currently only meaningful on macOS — on Windows / Linux the
        check_automation() probe returns True and this is a no-op."""
        if cls._perm_warning_emitted:
            return
        if platform.check_automation() is False:
            cls._perm_warning_emitted = True
            emit({
                "event": "warning",
                "code": "automation-denied",
                "message": "WillettBot can't watch app switches or "
                           + platform.get_file_manager_name() + " navigation — "
                           "the OS hasn't granted automation permission. Your "
                           "clicks and keystrokes are still recording, but "
                           "file-open and app-switch steps won't be detected. "
                           "On macOS: System Settings → Privacy & Security → "
                           "Automation → enable WillettBot for System Events, "
                           "Finder, and any apps you want it to control."
            })

    @classmethod
    def _query_frontmost_app(cls):
        out = platform.get_frontmost_app()
        if not out:
            cls._maybe_warn_about_permissions()
        return out

    @classmethod
    def _query_frontmost_window_title(cls):
        """Title of the frontmost window of the frontmost app, or ''.
        Used to anchor clicks to a specific window across replays."""
        return platform.get_frontmost_window_title()

    @classmethod
    def _query_finder_front_path(cls):
        """POSIX path of the front file-manager window's target folder.
        Despite the name (kept for backward compat with the rest of the
        recorder), this works for Finder, Explorer, or Linux file managers."""
        return platform.get_file_manager_front_path()

    @classmethod
    def _query_finder_selection(cls):
        """Path of the first-selected item in the front file-manager."""
        return platform.get_file_manager_selection()

    @classmethod
    def _query_window_count(cls, app_name):
        """Number of windows owned by `app_name`, or None if we can't tell.
        Used to detect when the user closes (X-outs) a window: the count
        drops while the frontmost app stays the same."""
        return platform.get_window_count(app_name)

    def _record_ctx(self, ts, marker):
        """Store a context marker AND emit a user-friendly live event."""
        with self._events_lock:
            self.events.append((ts, marker))
        emit({
            "event": "captured",
            "action": _friendly_context(marker),
            "context": True
        })

    def _ctx_poll_loop(self):
        """Background thread: watch frontmost app + Finder window path/selection
        while recording. Emits context markers on every meaningful change."""
        while not self.done_event.is_set():
            if self.state != 'recording':
                time.sleep(0.25)
                continue
            now = time.time()

            # Flush any in-flight horizontal-scroll burst that has gone quiet —
            # so a gesture gets emitted promptly even if no mouse/key event
            # triggers a sync flush.
            if (self._hscroll_sum != 0
                    and (now - self._hscroll_last_ts) > HSCROLL_BURST_WINDOW):
                self._flush_hscroll()
            app = self._query_frontmost_app() or self._ctx_prev_app

            # Refresh frontmost window title on every poll so clicks get
            # tagged with the *current* title, not whichever one we saw when
            # the app first came to the front. Errors / empty results leave
            # the previous title cached so transient osascript hiccups don't
            # drop the anchor.
            title = self._query_frontmost_window_title()
            if title:
                self._ctx_prev_window_title = title

            file_mgr = platform.get_file_manager_name()  # Finder / Explorer / Files

            if app and app != self._ctx_prev_app:
                # Special case: switching FROM the file manager TO another app
                # right after selecting a file usually means "user opened that
                # file with that app". Emit a file-open marker instead of a
                # plain app switch so replay opens the file directly.
                if (self._ctx_prev_app == file_mgr
                        and app != file_mgr
                        and self._ctx_prev_finder_sel):
                    # Folders should never inherit an app hint. If the user
                    # double-clicked a folder and then Chrome (or any other
                    # app) happened to momentarily come frontmost, we'd
                    # otherwise stamp `app: "Chrome"` on an open_file that
                    # points at a DIRECTORY. Replay would then run
                    # `open -a Chrome /path/to/folder` which loads the folder
                    # as a file:// URL in a Chrome tab. Bug seen in the wild.
                    import os as _os
                    sel = self._ctx_prev_finder_sel
                    try:
                        is_dir = _os.path.isdir(_os.path.expanduser(sel))
                    except Exception:
                        is_dir = False
                    if is_dir:
                        # The "selection" is the folder the user navigated
                        # INTO — they were browsing, not opening a file with
                        # another app. The folder nav itself was already
                        # captured by __ctx_finder_nav__, so emitting a
                        # redundant __ctx_file_open__ here would produce a
                        # phantom "open_file inPlace" duplicate AND swallow
                        # the actual app-switch we want recorded. Treat this
                        # as a normal app switch instead.
                        self._record_ctx(now, {
                            "action": "__ctx_app__",
                            "name":  app
                        })
                    else:
                        # Real file open — file selected in FM, then user
                        # switched apps. The destination app gets stamped
                        # so replay opens the file with the right app.
                        self._record_ctx(now, {
                            "action": "__ctx_file_open__",
                            "path": sel,
                            "app": app,
                        })
                else:
                    self._record_ctx(now, {
                        "action": "__ctx_app__",
                        "name":  app
                    })
                self._ctx_prev_app = app
                # App changed → the cached window title belongs to the old
                # app. Clear so we don't mis-anchor subsequent clicks.
                self._ctx_prev_window_title = None
                # Leaving the file manager invalidates its cached state.
                if app != file_mgr:
                    self._ctx_prev_finder_path = None
                    self._ctx_prev_finder_sel  = None

            # While the file manager is frontmost, watch for folder nav +
            # selection. The variable names keep "finder_" for backward
            # compatibility with the rest of the recorder; semantically these
            # mean "current file-manager state" on every platform.
            if app == file_mgr:
                fpath = self._query_finder_front_path()
                if fpath and fpath != self._ctx_prev_finder_path:
                    # Skip the VERY FIRST observation so we don't record a
                    # bogus "navigate to wherever the FM already was".
                    if self._ctx_prev_finder_path is not None:
                        # inPlace=true → replay retargets the existing FM
                        # window instead of spawning a new one every time.
                        self._record_ctx(now, {
                            "action":  "__ctx_finder_nav__",
                            "path":    fpath,
                            "inPlace": True
                        })
                        # Mark that the file manager just navigated. We freeze
                        # close-detection on the FM for ~1.5s after each nav
                        # because Finder's `count of windows` (and Explorer's
                        # equivalent) can briefly read low during the moment
                        # one folder view is replaced by another, producing a
                        # phantom window-close → phantom Cmd+W hotkey.
                        self._ctx_last_fm_nav_ts = now
                    self._ctx_prev_finder_path = fpath
                sel = self._query_finder_selection()
                if sel:
                    self._ctx_prev_finder_sel = sel

            # Window-close detection: if the window count for the frontmost
            # app drops AND stays dropped across THREE consecutive polls
            # (~0.6s), the user really X'd out a window. Anything shorter
            # gets dismissed as a transient flicker — tooltips, transient
            # panels, modal sheets opening/closing, system HUDs, file-manager
            # folder-transition gaps, etc., all cause brief window-count
            # blips on both Mac and Windows.
            #
            # Additionally, suppress this entire block for the file manager
            # while a recent nav is hot: rapid double-clicks through folders
            # were producing phantom Cmd+W's even with the old 2-poll rule.
            if app:
                # Suppression window: file manager just navigated -> skip.
                FM_NAV_FREEZE = 1.5    # seconds after a nav before close-detection re-arms
                CLOSE_POLLS_REQUIRED = 3
                fm_freeze = (
                    app == file_mgr
                    and (now - self._ctx_last_fm_nav_ts) < FM_NAV_FREEZE
                )
                if fm_freeze:
                    # Don't accumulate or commit drops — but still take the
                    # current count as the new baseline so we don't fire the
                    # moment the freeze ends (e.g. user closes a window
                    # immediately after navigating).
                    wc = self._query_window_count(app)
                    if wc is not None:
                        self._ctx_window_drop_pending[app] = 0
                        self._ctx_prev_window_count[app] = wc
                else:
                    wc = self._query_window_count(app)
                    prev = self._ctx_prev_window_count.get(app)
                    if wc is not None:
                        if prev is not None and wc < prev:
                            # Below previous count — increment the per-app
                            # "drop seen" counter but DON'T emit yet.
                            self._ctx_window_drop_pending[app] = \
                                self._ctx_window_drop_pending.get(app, 0) + 1
                            if self._ctx_window_drop_pending[app] >= CLOSE_POLLS_REQUIRED:
                                self._record_ctx(now, {
                                    "action": "__ctx_window_close__",
                                    "app":    app
                                })
                                self._ctx_window_drop_pending[app] = 0
                                # Lock in the new lower count as baseline so
                                # we don't keep firing on every subsequent poll.
                                self._ctx_prev_window_count[app] = wc
                        else:
                            # Count is steady or increased — clear any pending
                            # drop and update the baseline.
                            self._ctx_window_drop_pending[app] = 0
                            self._ctx_prev_window_count[app] = wc

            # Sleep until either the interval elapses OR a click kicks us.
            # Clearing afterward so the next pass starts with a fresh wake
            # signal (click → set → wait returns → clear → loop → next wake
            # can only come from the next click or the next interval).
            self._ctx_wake.wait(CONTEXT_POLL_INTERVAL)
            self._ctx_wake.clear()

    def compile(self):
        """Turn raw events into runner.py actions.
        - Consecutive 'char' events merge into one 'type' action.
        - 'move_to' events get a real-time duration so playback glides at
          the same speed the user moved (bounded so long pauses don't stall).
        - Gaps between *non-move* events >= MIN_WAIT become 'wait' actions.
          We skip inserting waits before move_to since the move's duration
          already provides pacing.
        - Context markers (__ctx_app__, __ctx_finder_nav__, __ctx_file_open__)
          are rewritten to focus_app / open_file. If a compatible action was
          recorded just before the marker (click that opened a folder,
          Cmd+Tab that switched apps, etc.), the marker REPLACES it so the
          replay uses the deterministic semantic step instead of fragile
          pixel coordinates.
        """
        actions = []
        ts_per  = []   # parallel list of timestamps for lookback-within-window
        pending_chars = []
        # Anchor point for relative timing. Fall back to time.time() if the
        # recorder somehow never received a start timestamp.
        if self.start_time is not None:
            last_ts = self.start_time
        elif self.events:
            last_ts = self.events[0][0]
        else:
            last_ts = time.time()

        def flush_text(ts):
            if pending_chars:
                actions.append({"action": "type", "text": ''.join(pending_chars)})
                ts_per.append(ts)
                pending_chars.clear()

        def maybe_wait(from_ts, to_ts):
            gap = to_ts - from_ts
            if gap >= MIN_WAIT:
                actions.append({"action": "wait", "seconds": round(gap, 2)})
                ts_per.append(to_ts)

        def _replace_nearest(ts_now, replaceable_kinds, new_action,
                             hotkey_matches=None):
            """Walk backward through `actions` and replace the most recent
            event whose kind is in `replaceable_kinds` (or a hotkey whose keys
            satisfy `hotkey_matches`) that happened within CONTEXT_COALESCE_WINDOW
            seconds. Cosmetic `move_to`/`wait` actions between the marker and
            the target are skipped (and discarded, since a focus_app /
            open_file replay doesn't need the trailing mouse glide to the
            dock icon or folder). Returns True if a replacement happened."""
            for i in range(len(actions) - 1, -1, -1):
                at = ts_per[i]
                if ts_now - at > CONTEXT_COALESCE_WINDOW:
                    return False
                a = actions[i]
                k = a.get('action')
                if k in replaceable_kinds:
                    actions[i] = new_action
                    ts_per[i] = ts_now
                    # Drop dangling mouse-glides leading up to the replaced
                    # event (on replay they'd just waggle the mouse before the
                    # semantic step). Preserve any `wait` — that represents a
                    # real pause the user took, and dropping it risks racing
                    # Finder/the target app before it's ready.
                    while i > 0 and actions[i-1].get('action') == 'move_to':
                        del actions[i-1]; del ts_per[i-1]; i -= 1
                    return True
                if hotkey_matches and k == 'hotkey' and hotkey_matches(a.get('keys') or []):
                    actions[i] = new_action
                    ts_per[i] = ts_now
                    while i > 0 and actions[i-1].get('action') == 'move_to':
                        del actions[i-1]; del ts_per[i-1]; i -= 1
                    return True
                if k in ('move_to', 'wait'):
                    continue
                # Anything else — we're not next to a click/hotkey.
                return False
            return False

        def _is_app_switch_hotkey(keys):
            # Cmd+Tab and Cmd+` (backtick) are the macOS app switchers.
            # Accept whichever way modifier names appear.
            if not keys:
                return False
            s = set(keys)
            has_cmd = bool(s & {'command','cmd'})
            return has_cmd and bool(s & {'tab', '`', 'grave'})

        # Sort events by timestamp — the context-polling thread appends from
        # a separate thread, so raw insertion order isn't guaranteed.
        events_sorted = sorted(self.events, key=lambda e: e[0])

        for ts, action in events_sorted:
            kind = action.get('action')

            # ── context markers ──────────────────────────────────────────
            if kind == '__ctx_app__':
                flush_text(ts)
                new = {"action": "focus_app", "name": action['name']}
                if not _replace_nearest(ts, ('click','double_click'), new,
                                        hotkey_matches=_is_app_switch_hotkey):
                    maybe_wait(last_ts, ts)
                    actions.append(new); ts_per.append(ts)
                last_ts = ts
                continue

            if kind == '__ctx_finder_nav__':
                flush_text(ts)
                # inPlace=true → replay navigates the existing Finder window
                # instead of spawning a new one each time (prevents the "ten
                # Finder windows pile up" problem on folder-drilling replays).
                new = {"action": "open_file", "path": action['path'],
                       "inPlace": bool(action.get('inPlace', True))}
                if not _replace_nearest(ts, ('double_click','click','press'), new):
                    maybe_wait(last_ts, ts)
                    actions.append(new); ts_per.append(ts)
                last_ts = ts
                continue

            if kind == '__ctx_file_open__':
                flush_text(ts)
                # File opens (Finder → app) spawn a new window by design, so
                # inPlace stays false here regardless of the helper default
                # — UNLESS the marker explicitly says otherwise, which is how
                # the poll thread signals "this is a folder, use Finder."
                new = {"action": "open_file", "path": action['path']}
                # The "app" hint is preserved only if meaningful (not empty).
                if action.get('app'):
                    new['app'] = action['app']
                if action.get('inPlace'):
                    new['inPlace'] = True
                if not _replace_nearest(ts, ('double_click','click','press'), new):
                    maybe_wait(last_ts, ts)
                    actions.append(new); ts_per.append(ts)
                last_ts = ts
                continue

            if kind == '__ctx_window_close__':
                flush_text(ts)
                # Cmd+W works universally on macOS to close the frontmost
                # window — way more reliable than trying to re-hit a close
                # button by pixel coordinate on replay. If the user already
                # pressed Cmd+W themselves, don't double up.
                def _is_cmd_w(keys):
                    s = set(keys or [])
                    return bool(s & {'command','cmd'}) and 'w' in s
                new = {"action": "hotkey", "keys": ["command", "w"]}
                # Skip emitting if the nearest recent event is already Cmd+W.
                already_cmd_w = False
                cutoff = ts - CONTEXT_COALESCE_WINDOW
                for i in range(len(actions) - 1, -1, -1):
                    if ts_per[i] < cutoff: break
                    a = actions[i]
                    k = a.get('action')
                    if k == 'hotkey' and _is_cmd_w(a.get('keys')):
                        already_cmd_w = True
                        break
                    if k in ('move_to', 'wait'): continue
                    break
                if already_cmd_w:
                    last_ts = ts
                    continue
                if not _replace_nearest(ts, ('click','double_click'), new):
                    maybe_wait(last_ts, ts)
                    actions.append(new); ts_per.append(ts)
                last_ts = ts
                continue

            # ── regular events ───────────────────────────────────────────
            if kind == 'char':
                if not pending_chars:
                    maybe_wait(last_ts, ts)
                pending_chars.append(action['char'])
                last_ts = ts
                continue

            flush_text(ts)

            if kind == 'move_to':
                # Duration = real-time gap since previous event, bounded.
                gap = ts - last_ts
                duration = max(MOVE_DURATION_MIN, min(MOVE_DURATION_MAX, gap))
                out = dict(action)
                out['duration'] = round(duration, 2)
                actions.append(out); ts_per.append(ts)
            else:
                maybe_wait(last_ts, ts)
                actions.append(action); ts_per.append(ts)
            last_ts = ts

        flush_text(last_ts)
        return self._polish(actions)

    def _polish(self, actions):
        """Post-compile cleanup: strip cosmetic noise from the script.

        Three rules, all safe:
          1. Drop every `move_to`. The cursor still gets where it needs to
             be because every click/drag specifies (x,y); the move_to
             actions were only there to reproduce the user's mouse GLIDE
             between meaningful events. On replay with a differently-laid-
             out screen those glides visibly wander through empty space
             before the next click fires, making the replay look janky
             even when it's functionally correct. Teleporting is cleaner.
          2. Drop `click` / `double_click` actions whose only purpose was
             to trigger a semantic action that already follows them — the
             classic case is a double-click on a Finder folder where the
             context poller DID detect the navigation but the pixel-click
             got left behind. If within the next 3 non-wait actions we see
             an `open_file`, the click is redundant; drop it.
          3. Drop a leading `focus_app: <file_manager>` when the next
             non-wait action is an `open_file inPlace=true`. The bare
             focus_app would pop up a default Finder/Explorer window
             showing whatever was last open before the inPlace open_file
             retargets it — the user sees an unwanted "regular Finder
             window prior to the first folder" flash. The open_file
             itself launches the FM if not already running, so the
             focus_app is redundant.

        Rule 2 intentionally preserves clicks followed by NON-open_file
        actions — those are real button-clicks inside apps (Calendar event,
        Safari button, etc.) and we must not drop them.
        """
        # Rule 1: strip move_to.
        stripped = [a for a in actions if a.get('action') != 'move_to']

        # File-manager name for rule 3 — Finder / File Explorer / Files / etc.
        # Stored as lower-case for case-insensitive matching against focus_app
        # names that came through any path (recorder vs runtime guesses).
        fm_name = (platform.get_file_manager_name() or '').lower()

        # Rule 2: drop click/double_click superseded by a following open_file.
        # Rule 3: drop focus_app:<file_manager> superseded by an inPlace open_file.
        out = []
        LOOKAHEAD = 4       # how far ahead to scan for a semantic action
        LOOKAHEAD_NONWAIT = 3
        for i, a in enumerate(stripped):
            kind = a.get('action')
            if kind == 'focus_app' and fm_name and \
                    (a.get('name') or '').lower() == fm_name:
                # Look ahead for the next non-wait action.
                superseded = False
                for j in range(i + 1, min(i + 1 + LOOKAHEAD, len(stripped))):
                    b = stripped[j]
                    bk = b.get('action')
                    if bk == 'wait':
                        continue
                    # Next semantic action is an inPlace open_file → drop us.
                    if bk == 'open_file' and b.get('inPlace'):
                        superseded = True
                    break  # only look at the FIRST non-wait action
                if superseded:
                    continue
            if kind in ('click', 'double_click'):
                # Scan forward, skipping waits (they represent real pauses
                # the user took between the click and whatever fired next).
                seen_nonwait = 0
                superseded_by = None
                for j in range(i + 1, min(i + 1 + LOOKAHEAD, len(stripped))):
                    b = stripped[j]
                    bk = b.get('action')
                    if bk == 'wait':
                        continue
                    seen_nonwait += 1
                    if bk == 'open_file':
                        superseded_by = b
                        break
                    if seen_nonwait >= LOOKAHEAD_NONWAIT:
                        break
                if superseded_by is not None:
                    # Drop this click — the open_file that follows does
                    # the real work and doesn't care where the cursor was.
                    continue
            out.append(a)
        return out


# Module-level helper used by _record_ctx to generate user-facing live events.
def _friendly_context(marker):
    kind = marker.get('action')
    if kind == '__ctx_app__':
        return {"action": "focus_app", "name": marker.get('name', '')}
    if kind == '__ctx_finder_nav__':
        out = {"action": "open_file", "path": marker.get('path', '')}
        if marker.get('inPlace'):
            out['inPlace'] = True
        return out
    if kind == '__ctx_file_open__':
        out = {"action": "open_file", "path": marker.get('path', '')}
        if marker.get('app'):
            out['app'] = marker['app']
        if marker.get('inPlace'):
            out['inPlace'] = True
        return out
    if kind == '__ctx_window_close__':
        # Close-window shortcut differs by OS. Mac uses Cmd+W; Windows and
        # Linux use Ctrl+W. We pick the right one at compile time so the
        # recorded script is portable to ANY install of WillettBot. (Even
        # though scripts are usually replayed on the same machine they were
        # recorded on, this keeps cross-machine sharing working.)
        mod = 'command' if platform.is_mac() else 'ctrl'
        return {"action": "hotkey", "keys": [mod, "w"]}
    return marker


# ── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-hotkey', default='f9')
    parser.add_argument('--end-hotkey', default='f10')
    parser.add_argument('--name', default='Recorded Script')
    parser.add_argument('--description',
                        default='Recorded via the mirroring feature.')
    # Skip the "press start hotkey to begin" wait and go straight into
    # recording. Used by the UI's manual "Start recording" button so users
    # whose Fn / Input-Monitoring setup blocks the hotkey can still record.
    # SIGTERM still triggers a clean compile+save.
    parser.add_argument('--auto-start', action='store_true')
    args = parser.parse_args()

    if args.start_hotkey == args.end_hotkey:
        emit({"event": "error",
              "message": "Start and end hotkeys must be different."})
        return

    emit({"event": "ready",
          "startHotkey": args.start_hotkey,
          "endHotkey": args.end_hotkey,
          "autoStart": bool(args.auto_start)})

    # Fire the Automation probe in a background thread so a 10-second osascript
    # block (e.g. macOS showing the AppleEvents permission prompt) doesn't
    # delay the listeners. The listeners start immediately and capture
    # clicks/keys in parallel; automation_status emits when the probe finishes.
    #
    # ALSO writes the probe result to ~/Library/Logs/WillettBot/automation.log
    # as a fallback — if the hub.html event log filters unknown event types
    # (which is what happened on mom's Mac), we can still grab the diagnostic
    # from the file. The file is tiny (<1KB per probe), truncated on each run
    # so it never grows, and lives in a user-accessible path that doesn't
    # require Terminal wizardry to find (open Finder → ⌘⇧G → paste path).
    def _run_probe():
        import os, datetime
        try:
            probe = probe_automation_permission()
        except Exception as e:
            probe = {'status': 'error', 'returncode': None,
                     'stdout': '', 'stderr': 'probe crashed: ' + str(e),
                     'osascript': False}
        _msgs = {
            'ok':      'Automation permission OK — app-context and file-open detection active.',
            'denied':  'macOS denied Automation access. Clicks still record, but app switches and folder opens won\'t be detected. Run in Terminal: tccutil reset AppleEvents com.willett.willettbot && sudo killall tccd — then reboot and try again.',
            'silent':  'osascript ran but returned nothing — probably a ghost TCC entry silently dropping AppleEvents. Run in Terminal: tccutil reset AppleEvents com.willett.willettbot && sudo killall tccd — then reboot and try again.',
            'timeout': 'Waiting for macOS Automation prompt — look for the popup and click Allow.',
            'error':   'Couldn\'t run /usr/bin/osascript. Context detection disabled.'
        }
        status_event = {
            "event":       "automation_status",
            "status":      probe['status'],
            "returncode":  probe['returncode'],
            "stdout":      probe['stdout'],
            "stderr":      probe['stderr'],
            "osascript":   probe['osascript'],
            "message":     _msgs.get(probe['status'], '')
        }
        emit(status_event)
        # File log — survives regardless of whether the UI renders the event.
        # Path is ~/Library/Logs/WillettBot/automation.log (standard macOS
        # app-log location, shows up in Console.app too).
        try:
            log_dir = os.path.expanduser('~/Library/Logs/WillettBot')
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, 'automation.log')
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('WillettBot Automation probe\n')
                f.write('timestamp: ' + datetime.datetime.now().isoformat() + '\n')
                f.write('python:    ' + sys.executable + '\n')
                f.write('---\n')
                f.write(json.dumps(status_event, indent=2) + '\n')
        except Exception:
            # Never let log-writing failures block the recorder.
            pass
    threading.Thread(target=_run_probe, daemon=True).start()

    rec = Recorder(args.start_hotkey, args.end_hotkey)
    if args.auto_start:
        # Flip directly into 'recording'. The start hotkey is still live (so
        # users can also press it — harmless in this state), but no longer
        # required. Emit 'started' so the hub shows the recording UI.
        rec.state = 'recording'
        rec.start_time = time.time()
        emit({"event": "started"})

    # SIGTERM from the Electron parent should end the recording cleanly.
    # NOTE: this works on macOS / Linux where Node's process.kill('SIGTERM')
    # delivers a real POSIX signal. On Windows, Node's .kill() always maps to
    # TerminateProcess() — Python never gets a chance to run this handler
    # before being killed. The stdin watcher below is the cross-platform
    # backstop: parent writes "stop\n" to our stdin and we finalize cleanly.
    def _sigterm(signum, frame):
        rec.done_event.set()
    try:
        signal.signal(signal.SIGTERM, _sigterm)
    except Exception:
        pass

    # Cross-platform stop channel: parent process writes a line containing
    # "stop" to our stdin to request a clean finalize. This is the ONLY
    # mechanism that works on Windows (see comment above). On Mac/Linux it
    # arrives in parallel with SIGTERM; whichever wins the race is fine,
    # both end up calling rec.done_event.set() and exit is idempotent.
    def _stdin_stop_watcher():
        try:
            for line in sys.stdin:
                if 'stop' in line.strip().lower():
                    rec.done_event.set()
                    return
        except Exception:
            # stdin closed / detached / not a real pipe — fall through.
            # Recorder still exits via the end hotkey or SIGTERM.
            pass
    threading.Thread(target=_stdin_stop_watcher, daemon=True).start()

    kb_listener = keyboard.Listener(on_press=rec.on_key_press,
                                    on_release=rec.on_key_release)
    ms_listener = mouse.Listener(on_click=rec.on_click,
                                 on_move=rec.on_move,
                                 on_scroll=rec.on_scroll)
    kb_listener.start()
    ms_listener.start()

    # Context poller runs as a daemon thread — it exits automatically when
    # done_event fires. It's responsible for the semantic focus_app /
    # open_file upgrades that make multi-app and folder-drilling scripts work.
    ctx_thread = threading.Thread(target=rec._ctx_poll_loop, daemon=True)
    ctx_thread.start()

    try:
        while not rec.done_event.is_set():
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        try: kb_listener.stop()
        except Exception: pass
        try: ms_listener.stop()
        except Exception: pass
        # ctx_thread is a daemon — no explicit stop needed once done_event is set.

    # Final flush: any trailing h-scroll burst that didn't hit the poll tick.
    rec._flush_hscroll()
    actions = rec.compile()
    script = {
        "name": args.name,
        "description": args.description,
        "variables": {},
        "actions": actions
    }
    emit({"event": "done", "script": script})


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        emit({
            "event": "error",
            "message": str(e),
            "trace": traceback.format_exc()
        })
    sys.exit(0)
