# WillettBot — Windows Daily Playbook

Short, repeatable loop for working on / testing the app on Windows. Past
first-time setup — Git, Node, Python, the cloned repo, and `npm install`
are already done.

If something blows up, open <https://claude.ai> in Edge and paste the
error — same Claude as on the Mac side, just no file access.

---

## The loop (every session)

### 1. Open a fresh Command Prompt
Win+R → type `cmd` → Enter.

### 2. Pull the latest fixes from GitHub
```
cd %USERPROFILE%\willettbot
git pull
```
Expect either `Updating xxx..yyy` (good — new fixes pulled) or
`Already up to date.` (also fine).

If git complains about local changes:
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
and try again.

### 4. **Open the Developer Tools** (you'll need this for testing)
With WillettBot open: **Help menu → Toggle Developer Tools** (or
Ctrl+Shift+I). A panel opens. Click the **Console** tab. Leave it open
the whole session — it shows every error and diagnostic line.

---

## Test pass — run through these in order each session

### A. Confirm seed scripts are present

In the Dev Tools Console you should see lines like:
```
[willettbot seeder] seedDir = C:\Users\you\willettbot\scripts
[willettbot seeder] SCRIPTS_DIR = C:\Users\you\AppData\Roaming\WillettBot\scripts
[willettbot seeder] seed files found: example_greet.json, send_gmail.json
[willettbot seeder] copied send_gmail.json → ...
```

**Three things to check:**
- `seed files found:` includes `send_gmail.json`. If it doesn't, your
  cloned repo is missing the file — run `git pull` again.
- One of: `copied send_gmail.json` (first launch) OR no copy line for it
  (already exists from a previous launch). Either is fine.
- No `[willettbot seeder] write FAILED` lines. If you see one, paste it
  to claude.ai — it tells us exactly what permissions issue blocked the
  copy.

Then go to the **Builder / Scripts** view in WillettBot. The "Send Gmail"
script should be in the list now.

### B. Test the Gmail preset (popup + URL)

1. From the hub, click the **Send Gmail** card.
2. The popup window should appear in the **top-right corner** of your
   screen (same place macOS notifications show on Mac).
   - On old builds this was invisible because of a transparent-window bug.
     Latest build uses an opaque dark popup on Windows.
3. Fill in To / Subject / Body, click **Run Now**.
4. A new browser tab should open with Gmail compose **fully prefilled** —
   not just `https://mail.google.com/mail/?view=cm` with no fields. If
   you only see a bare compose tab, you're on an old build (cmd.exe was
   chopping the URL at `&`); `git pull` and try again.

### C. Test the recorder hotkey fix

1. Click **Record New Script.**
2. Press **F9** (or click the Start button) to begin recording.
3. Open Notepad, type "hello", press **Ctrl+C**, press **Ctrl+V**.
4. Click **Stop** OR press **F10**.
5. The Save Script panel should appear. Save it with a name.
6. Open the saved script in the Builder.

**What to check:**
- The hotkey actions should show as `ctrl+c` and `ctrl+v` — not blank,
  not "ctrl+d", not weird symbols.
- In the **Dev Tools Console**, look for a line like:
  ```
  [recorder dbg] first hotkey: vk=67 char='\x03' resolved='c' mods=['ctrl']
  ```
  - `vk=67` (or `vk=86` for V) means pynput delivered the virtual key
    code correctly.
  - `resolved='c'` means our recorder mapped it to the right letter.

**If hotkeys are still wrong**, copy that whole `[recorder dbg]` line and
paste it to claude.ai — it tells me exactly what pynput is giving us so
I can fix it definitively.

### D. Test the recorder Stop button

When you click **Stop** during recording (instead of pressing F10), the
**Save Script** panel must appear within a couple seconds. If it doesn't,
you're on an old build — `git pull` and try again.

### E. Confirm Windows doesn't show Mac-only UI

These should NOT appear on Windows:
- Any banner saying "macOS will ask for Accessibility…"
- Any "Fix permissions" link
- "System Settings → Privacy & Security" instructions

If you see any of those on Windows, you're on an old build — `git pull`.

---

## What to send back if anything's wrong

For each broken item, copy from the Dev Tools Console:
1. The `[willettbot seeder]` lines (top of the console).
2. The `[recorder dbg]` line (after recording any hotkey).
3. Any red error lines.

Paste those into <https://claude.ai> in Edge. With those three things
I can diagnose nearly everything without seeing the screen.

---

## Common issues

**Stop button doesn't open the Save Script panel**
Old build. `git pull`. Workaround until you pull: press F10 (end hotkey)
instead of clicking Stop.

**Hotkeys recorded as the wrong letter or blank**
Old build. `git pull`. After pulling, the `[recorder dbg]` line in the
console will show what pynput delivered.

**Gmail preset opens a half-broken URL or no popup at all**
Old build. `git pull`. The fixes are in the latest commit.

**"npm start" hangs or errors at "creating venv"**
The system Python is missing or not on PATH. Run `python --version` —
if that errors, re-run the python.org installer with "Add python.exe to
PATH" checked.

**"pywin32 import failed" in the dev console**
The venv bootstrap didn't finish. Delete `%APPDATA%\WillettBot\venv\`
and `npm start` again to re-bootstrap.

**Smoke test (`python platform_helpers.py`) prints `frontmost app: (empty)`**
Your shell's Python doesn't have pywin32. Either:
```
py -m pip install pywin32
```
…or run the smoke test through the app's venv Python:
```
"%APPDATA%\WillettBot\venv\Scripts\python.exe" platform_helpers.py
```

**Clicks "happen" on replay but nothing visibly moves**
Cursor is in a screen corner (pyautogui FAILSAFE). Move it away.

**"GetForegroundWindow() returns 0"**
Foreground window is the desktop or a UAC dialog. Click on a normal app
window first.

**Recorded script saves but replay does nothing**
Open Help → Toggle Developer Tools → Console tab. Errors during the
Python runner spawn show up there.

**`git pull` says "Your local changes would be overwritten"**
You edited a tracked file on Windows without committing it.
```
git stash
git pull
git stash pop
```

---

## The fix-and-test loop

When something breaks on Windows that you want me to fix from the Mac:

1. **On Windows:** copy the exact error or `[recorder dbg]` / seeder
   lines from the Dev Tools Console.
2. **On Mac:** open <https://claude.ai>, paste it, ask for a fix.
3. **On Mac:** I push the fix to GitHub.
4. **On Windows:** `git pull` → close the app → `npm start` again.
5. Re-test from "Test pass" above.

Mac is the source of truth; Windows is a copy you keep refreshing.
