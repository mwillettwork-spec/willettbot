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
**Console** tab. Leave open the whole session.

---

## After this latest pull — what changed

This pull adds:
1. The seed scripts (`send_gmail.json`, `example_greet.json`) are now
   tracked in git via a new `scripts-seed\` folder. Before this they were
   in `scripts\` which is gitignored — they never made it to your Windows
   side, which is why Send Gmail wasn't in the builder.
2. Windows COM is initialized correctly per-thread now, so the recorder
   can detect Explorer folder/file opens (was failing silently before).

---

## Test pass — do these in order

### A. Both seed scripts now appear

In the **Builder / Scripts** view, you should see:
- **Send Gmail**
- **Greet by name** (or similar — the example_greet script)
- **Auto Clicker** (built-in, always there)

In the Dev Tools Console at startup you should see:
```
[willettbot seeder] seedDir = ...\willettbot\scripts-seed
[willettbot seeder] seed files found: example_greet.json, send_gmail.json
[willettbot seeder] copied send_gmail.json → ...
[willettbot seeder] copied example_greet.json → ...
```

If the scripts STILL aren't there: paste me the entire `[willettbot seeder]`
block. Most likely the `seedDir` doesn't point at the new folder for some
reason.

### B. Test the Send Gmail preset

1. Click **Send Gmail** in the Builder (or the card on the hub).
2. The script-launch popup should appear in the **center** of the window
   asking for To / Subject / Body — these are the script's "variables."
3. Fill them in, click **Run Now**.
4. While the script runs, a **second popup** appears in the **top-right
   corner** of your screen asking you to confirm you sent the email.
   This is the recorder's `prompt` action.
   - If this popup is invisible / never appears, you're on an old build —
     `git pull` and try again.
5. A new browser tab should open with **Gmail compose fully prefilled** —
   not just `https://mail.google.com/mail/?view=cm` with no fields. If
   only the URL prefix opens, you're on an old build (Windows cmd was
   chopping the URL at `&`).
6. Click "I sent it" on the popup to finish.

### C. Test the recorder hotkey

1. Click **Record New Script.**
2. Press F9 (or click Start) to begin recording.
3. Open Notepad, type "hello", press **Ctrl+C**, press **Ctrl+V**.
4. Click **Stop** OR press **F10**.
5. The Save Script panel should appear. Save it with a name.
6. Open the saved script in the Builder.

In the actions list you should see `ctrl+c` and `ctrl+v` — not blank, not
"ctrl+d", not weird symbols.

In the Console look for:
```
[recorder dbg] first hotkey: vk=67 char='\x03' resolved='c' mods=['ctrl']
```
- `vk=67` is the virtual key code for C
- `resolved='c'` means we mapped it correctly

If the hotkey is STILL wrong, paste me that whole `[recorder dbg]` line.

### D. Test recorder folder/file detection

1. Click **Record New Script** again.
2. Start recording.
3. Open File Explorer (Win+E).
4. Navigate into a folder (e.g. Downloads → some-subfolder).
5. Double-click a file inside it (any text file, image, whatever).
6. Stop recording.
7. Open the saved script.

In the actions list you should see things like:
- `navigate to C:\Users\you\Downloads\some-subfolder` (the `inPlace`
  folder navigation)
- `open C:\Users\you\Downloads\some-subfolder\thefile.txt` (the file open)

NOT a long list of raw `click (1234, 567)` actions. If you only see raw
clicks, paste me the action list and I'll dig in.

### E. Confirm Windows shows no Mac-only UI

Should NOT appear anywhere on Windows:
- "macOS will ask for Accessibility…"
- "System Settings → Privacy & Security"
- "Fix permissions" link

If you see any of these, you're on an old build — `git pull`.

---

## What to send me if anything's off

For each issue, copy from the Console:
1. The `[willettbot seeder]` lines (top of console).
2. The `[recorder dbg]` line (after recording any hotkey).
3. The action list of any test recording where the result was wrong.
4. Any red error lines.

Paste those into <https://claude.ai>. I can debug nearly everything from
those four pieces without seeing your screen.

---

## Common issues

**Send Gmail / Greet by name still missing**
You're on a build before the `scripts-seed/` switch. `git pull`. The
seeder's first launch will copy them in.

**Stop button doesn't open the Save Script panel**
Old build. `git pull`. Workaround: press F10 instead of clicking Stop.

**Hotkeys recorded as the wrong letter**
Old build. `git pull`. After pulling, the `[recorder dbg]` Console line
shows what pynput actually delivered.

**Folder/file opens don't show up in recorded scripts**
Old build (COM init missing in the polling thread). `git pull`.

**No prompt popup during a script run**
Old build (transparent toast was invisible on Windows). `git pull`.

**"npm start" hangs at "creating venv"**
System Python missing or not on PATH. Run `python --version` — if it
errors, re-run the python.org installer with "Add python.exe to PATH"
checked.

**"pywin32 import failed" in the dev console**
Venv bootstrap didn't finish. Delete `%APPDATA%\WillettBot\venv\` and
`npm start` again to re-bootstrap.

**Smoke test prints `frontmost app: (empty)`**
Your shell's Python doesn't have pywin32. Either:
```
py -m pip install pywin32
```
…or run the smoke test through the app's venv Python:
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
