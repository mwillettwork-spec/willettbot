# WillettBot — Windows Daily Playbook

You're past first-time setup. This is the short loop you'll run every time
you boot into Windows and want to work on / test the app.

---

## The loop (do this every session)

### 1. Open a fresh Command Prompt
Win+R → type `cmd` → Enter.

### 2. Pull the latest fixes from GitHub
```
cd %USERPROFILE%\willettbot
git pull
```

You should see either:
- `Updating xxxxxxx..yyyyyyy` followed by a list of changed files — new
  fixes pulled in, keep going.
- `Already up to date.` — nothing new since last time, also fine.

If it complains "Your local changes would be overwritten," you edited
something on the Windows side without committing. Run:
```
git stash
git pull
git stash pop
```

### 3. Launch the app
```
npm start
```

If `npm start` errors about missing Node modules, run `npm install` once
and try again — that means a `git pull` brought in new JS dependencies.

### 4. Test that things still work

**Quick recorder check:**
1. Click "Record New Script."
2. Press F9 to start (or whatever your start hotkey is).
3. Do something tiny — open Notepad, type "hello", press Ctrl+C, press Ctrl+V.
4. Click **Stop** OR press **F10**.
5. The "Save Script" panel should appear. Save it with a name.
6. Open the saved script and check:
   - Hotkeys show as `ctrl+c` and `ctrl+v` (not weird letters or blanks).
   - Clicks have proper coordinates.
7. Click **Replay** and watch it run.

**Quick Gmail preset check:**
1. From the hub, run the "Send Gmail" preset (or whatever it's labeled).
2. A new browser tab should open with Gmail compose, with To / Subject /
   Body all filled in.
3. If only the URL prefix opens (no fields populated), you're on an old
   build — `git pull` and try again.

---

## Common issues

**Stop button doesn't open the Save Script panel**
You're on an old build. `git pull` to get the fix. Workaround: press F10
(your end hotkey) instead of clicking Stop — the hotkey path always works.

**Hotkeys recorded as the wrong letter (or blank)**
You're on an old build. `git pull` to get the vk-based hotkey fix.

**Gmail preset opens a half-broken URL**
You're on an old build. `git pull` to get the URL-handling fix that
stops cmd.exe from chopping the URL at `&`.

**"npm start" hangs or errors at "creating venv"**
The system Python is missing or not on PATH. Open Command Prompt and run
`python --version` — if that errors out, the python.org installer didn't
add Python to PATH. Re-run the installer and check "Add python.exe to PATH".

**"pywin32 import failed" in the dev console**
The venv bootstrap didn't finish cleanly. Delete `%APPDATA%\WillettBot\venv\`
and restart `npm start` to re-bootstrap.

**Smoke test (`python platform_helpers.py`) prints `frontmost app: (empty)`**
Your shell's Python doesn't have pywin32. Either install it:
```
py -m pip install pywin32
```
…or run the smoke test through the app's venv Python (which always has it):
```
"%APPDATA%\WillettBot\venv\Scripts\python.exe" platform_helpers.py
```

**Clicks "happen" on replay but nothing visibly moves**
Cursor is in a screen corner triggering pyautogui's FAILSAFE corner
detection. Move it away and try again.

**"GetForegroundWindow() returns 0"**
The foreground window is the desktop or a UAC dialog. Click on a normal
app window (Notepad, Explorer) first.

**Recorded script saves but replay does nothing**
Open Help menu → Toggle Developer Tools → Console tab. Errors during the
Python runner spawn show up there with a real stack trace.

---

## When you're stuck

Open <https://claude.ai> in Edge, paste:
1. Which step you were on.
2. The exact error message or screenshot.
3. The output of `python --version` and `node --version`.

I can debug from there even without file access on the Windows side.

---

## The fix-and-test loop (when something breaks on Windows)

1. **On Windows:** copy the exact error from the dev console / terminal.
2. **On Mac:** open <https://claude.ai>, paste the error, ask for a fix.
3. **On Mac:** apply the fix, then commit + push to GitHub.
4. **On Windows:** `git pull` → close the app → `npm start` again.
5. Re-test.

Mac stays the source of truth; Windows is a copy you keep refreshing
with `git pull`.
