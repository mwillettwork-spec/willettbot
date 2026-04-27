# WillettBot — Windows Daily Playbook

Short, repeatable loop for working on / testing the app on Windows.

If something blows up, open <https://claude.ai> in Edge and paste the
error — same Claude as on the Mac side, just no file access.

---

## The loop (every session)

### 1. Open a fresh Command Prompt
Win+R → `cmd` → Enter.

### 2. Pull the latest fixes
```
cd %USERPROFILE%\willettbot
git pull
```

### 3. Launch the app
```
npm start
```
If npm errors about missing modules, run `npm install` once and try again.

### 4. Open Developer Tools
**Help menu → Toggle Developer Tools** (or **Ctrl+Shift+I**) → click the
**Console** tab. Leave it open the whole session.

---

## What changed in this latest pull

1. **Greeting script is cross-platform now.** It opens TextEdit on Mac,
   Notepad on Windows, gedit on Linux, by way of a new `{{TEXT_EDITOR}}`
   variable resolved at runtime. The Cmd+N → Ctrl+N translation is also
   automatic via `{{MOD}}`.
2. **Hotkey false-positives fixed.** The recorder now sanity-checks its
   modifier state against the OS's real key state on every keypress, so
   a missed Ctrl-release no longer causes plain letters to record as
   bogus Ctrl+letter hotkeys.
3. **Explorer reuses one window now.** `inPlace` folder navigations
   retarget the existing Explorer window instead of spawning a new one
   per folder. The first folder opens a window; every subsequent
   navigation in the same script reuses it.
4. **Tighter timing.** Recorder now keeps any pause ≥ 0.15s (was 0.4s),
   so playback tracks user-input timing much more closely.
5. **Shorter Gmail prompt.** The "click Send" confirm popup is now one
   sentence instead of a paragraph.

---

## Test pass — do these in order

### A. Confirm both seed scripts are present and updated

Builder / Scripts view should show:
- **Send Gmail** (with shorter prompt copy)
- **Greet by name** (renamed from "Greet in TextEdit")
- **Auto Clicker** (built-in)

In Console at startup:
```
[willettbot seeder] seedDir = ...\willettbot\scripts-seed
[willettbot seeder] seed files found: example_greet.json, send_gmail.json
```

If you don't see the new names, the seeder didn't upgrade — try deleting
`%APPDATA%\WillettBot\scripts\example_greet.json` and
`%APPDATA%\WillettBot\scripts\send_gmail.json` and relaunching to force a
re-seed.

### B. Greeting script works on Windows

1. Click **Greet by name** in the Builder.
2. Pop-up asks for a name. Fill in something, click **Run Now**.
3. Notepad opens, then a new blank document, then types
   `Hello <name> — this was written by WillettBot.`

If Notepad doesn't open: in the Console, the runner emits a log line per
action — find the one that says `Opened Notepad` or `Failed: ...`. Paste
me the failure.

### C. Send Gmail works with shorter popup

1. Click **Send Gmail**.
2. Fill in To / Subject / Body, click **Run Now**.
3. Browser opens with Gmail compose prefilled.
4. The confirm popup appears top-right and just says **"Review the email
   in your browser and click Send."** Click **Done** to finish.

### D. Hotkey recording is reliable now

1. Click **Record New Script.**
2. Press F9 (or click Start) to begin.
3. Type `hello`, press **Ctrl+C**, press **Ctrl+V**.
4. Click **Stop** OR press F10.
5. Open the saved script.

You should see `ctrl+c` then `ctrl+v` — not blank, not "ctrl+d", not
random letters. AND no bogus hotkey events for plain typed letters.

In the Console, look for the debug line:
```
[recorder dbg] first hotkey: vk=67 char='\x03' resolved='c' mods=['ctrl']
```

### E. Explorer doesn't pile up windows

1. Click **Record New Script.**
2. Start recording.
3. Open File Explorer (Win+E).
4. Navigate Downloads → some-subfolder → some-deeper-folder.
5. Stop recording, save it.
6. Click **Replay**.

You should see ONE Explorer window navigating through the folders, not
three separate windows piling up.

In the Console you'll see lines like:
```
Navigated Explorer → C:\Users\you\Downloads
Navigated Explorer → C:\Users\you\Downloads\some-subfolder
```

If a fresh window spawns for each step, paste me the action list — the
recorder may not be marking inPlace correctly for some reason.

### F. Timing matches what you recorded

Record yourself doing something with deliberate pauses (e.g. open Notepad,
wait two seconds, type "hi", wait a second, click somewhere, wait, close).
Replay it. The replay should feel about the same speed as the recording —
not jumping straight from action to action with no pauses.

If it feels too fast, the recorder may be dropping waits. Open the saved
script and check that `wait` actions exist between the steps.

---

## What to send me if anything's off

For each issue, copy from the Console:
1. The `[willettbot seeder]` lines (top of console).
2. The `[recorder dbg]` line (after recording any hotkey).
3. The action list from any test recording where the result was wrong.
4. Any red error lines.

Paste those into <https://claude.ai>. With those four things I can
diagnose nearly everything without seeing your screen.

---

## Common issues

**Greeting script types into the wrong app**
The `{{TEXT_EDITOR}}` variable defaulted to a different app. Check the
Console for `Opened ...` — if it says something other than Notepad on
Windows, paste me the line.

**Hotkeys ghost-fire (random hotkey events you didn't press)**
Old build. `git pull`. The fix syncs modifier state with the OS so a
missed key-release can't make plain letters look like hotkeys.

**Recorded folder paths spawn N windows on replay**
Old build. `git pull`. The fix retargets ANY existing Explorer window
for `inPlace` navigations instead of only the foreground one.

**Replay feels too fast / skips pauses**
Old build with the 0.4s wait threshold. `git pull` for the 0.15s one.

**Stop button doesn't open the Save Script panel**
Old build. `git pull`. Workaround: press F10 instead of clicking Stop.

**No prompt popup during a script run**
Old build (transparent toast was invisible on Windows). `git pull`.

**"npm start" hangs at "creating venv"**
System Python missing or not on PATH. Run `python --version` — if it
errors, re-run the python.org installer with "Add python.exe to PATH".

**"pywin32 import failed" in the dev console**
Venv bootstrap didn't finish. Delete `%APPDATA%\WillettBot\venv\` and
`npm start` again to re-bootstrap.

**Smoke test prints `frontmost app: (empty)`**
Your shell's Python doesn't have pywin32. Either:
```
py -m pip install pywin32
```
…or run the smoke test through the app's venv:
```
"%APPDATA%\WillettBot\venv\Scripts\python.exe" platform_helpers.py
```

**`git pull` says "Your local changes would be overwritten"**
You edited a tracked file on Windows without committing it.
```
git stash
git pull
git stash pop
```

---

## The fix-and-test loop

When something breaks on Windows that needs a Mac-side fix:

1. **On Windows:** copy the relevant Console lines.
2. **On Mac:** open <https://claude.ai>, paste them, ask for a fix.
3. **On Mac:** I push the fix to GitHub.
4. **On Windows:** `git pull` → close the app → `npm start` again.
5. Re-test from "Test pass" above.
