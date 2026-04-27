# WillettBot — Windows Setup & Test Guide

This is your playbook for working on the Windows side. It covers two paths:

- **Section A** — first-time setup on a brand-new Windows machine.
- **Section B** — what to do every time you boot back into Windows (the
  daily loop: pull latest, launch the app, test).

If something blows up partway through, open <https://claude.ai> in Edge and
paste the error — same Claude as on the Mac side, just no file access.

---

## Where to start

- **First boot into Windows ever?** → go to **Section A** (everything from
  scratch: install Git / Node / Python, clone the repo, run the app).
- **Already finished Section A in a previous session?** → skip to
  **Section B**. That's the short, repeatable "I just rebooted, what now?"
  flow.

---

# SECTION A — First-time setup (only do this once)

You only need to run through Section A on a brand-new Windows machine.
Once it's done, every future session uses Section B instead.

## A1. Install prerequisites

Three things you already have on Mac. Total time: ~15 minutes including
downloads.

### A1a. Git for Windows
Download: <https://git-scm.com/download/win>
- Run the installer with all defaults.
- During install: when it asks about "Adjusting your PATH environment,"
  pick **"Git from the command line and also from 3rd-party software"**.

**Verify:** open Command Prompt (Win+R → `cmd`) and run:
```
git --version
```
Should print something like `git version 2.45.x`.

### A1b. Node.js (LTS, version 20 or higher)
Download: <https://nodejs.org/en/download>
- Pick the **LTS** "Windows Installer (.msi)" for x64.
- Run with all defaults — leave "Add to PATH" checked, leave the optional
  Chocolatey/Python add-on **unchecked** (we install Python separately).

**Verify:**
```
node --version
npm --version
```
Both should print versions. Node ≥ 20, npm ≥ 10.

### A1c. Python 3.12 (the python.org installer, NOT the Microsoft Store version)

⚠️ The Microsoft Store version of Python has registry shenanigans that break
pywin32. Use the python.org installer.

Download: <https://www.python.org/downloads/windows/>
- Pick **Python 3.12.x** (or 3.11.x — anything 3.10+ works).
- Run the installer.
- **CRITICAL:** check both boxes on the first screen:
  - ☑ Add python.exe to PATH
  - ☑ Use admin privileges when installing py.exe
- Click "Install Now" (default location is fine).

**Verify:** open a NEW Command Prompt (the old one won't see PATH changes):
```
python --version
pip --version
```
Should print Python 3.12.x and pip 24.x.

## A2. Clone the repo

You'll need a GitHub Personal Access Token (PAT) since the repo is private.
Find it in your password manager, or generate a new one at
<https://github.com/settings/tokens> (classic, with `repo` scope).

In Command Prompt:
```
cd %USERPROFILE%
git clone https://github.com/mwillettwork-spec/willettbot.git
cd willettbot
```

When git prompts for credentials, use your GitHub username + the PAT as the
password. Windows will remember it after the first clone.

**Verify:**
```
dir
```
Look for `main.js`, `recorder.py`, `runner.py`, `platform_helpers.py`,
`platform_win.py`, etc.

## A3. Install Node + Python dependencies

```
npm install
```

This pulls Electron and electron-builder. Takes 1–3 minutes. Warnings about
deprecated transitive deps are harmless.

**Verify:** the `node_modules\` directory exists and has hundreds of
subfolders.

## A4. First launch — bootstraps the Python venv

```
npm start
```

What happens:
1. Electron launches WillettBot.
2. The app sees no bundled Python, so it bootstraps a venv at
   `%APPDATA%\WillettBot\venv\` and pip-installs `pyautogui`, `pynput`,
   and `pywin32` into it. **First launch takes ~30 seconds.**
3. The hub UI appears.

If the venv bootstrap fails, the most common cause is python not being on
PATH — re-check Step A1c.

**You're done with first-time setup.** From now on, follow Section B every
time you boot into Windows.

---

# SECTION B — Returning to work (every session)

You finished Section A in a previous session. Today you booted Windows and
want to keep working. Run through these steps in order — they're short.

## B1. Open a fresh Command Prompt

Win+R → type `cmd` → Enter.

(A "fresh" one matters because old Command Prompt windows may have stale
PATH from before you installed Python / Node / Git.)

## B2. Pull the latest code from GitHub

This is the step that catches whatever fixes got pushed from the Mac side
since you last worked on Windows.

```
cd %USERPROFILE%\willettbot
git pull
```

You should see lines like `Updating xxxxxxx..yyyyyyy` followed by a list of
files that changed. If it just says `Already up to date.`, no new commits
were pushed since your last session — that's fine, move on.

If `git pull` complains about local changes, you probably edited something
on the Windows side and didn't commit it. Either commit it or stash it:
```
git stash
git pull
git stash pop
```

## B3. Launch the app

```
npm start
```

The app should come up in a few seconds (no venv re-bootstrap needed —
that only happens the first time, or if you delete `%APPDATA%\WillettBot\venv\`).

If `npm start` errors with something about missing Node modules, run
`npm install` once and try again — that means a `git pull` brought in new
JS dependencies.

## B4. Smoke-test the platform layer

Before testing record + replay, confirm the Windows backend is alive. Open
a SECOND Command Prompt (leave the app running in the first one) and run:

```
cd %USERPROFILE%\willettbot
python platform_helpers.py
```

Expected output (something like):
```
PLATFORM_NAME:        Windows
NATIVE_SCRIPT_ACTION: powershell
python:               C:\Users\you\AppData\Local\Programs\Python\Python312\python.exe
---
frontmost app:        Cmd        (or whatever app is in front)
frontmost title:      Command Prompt
file manager name:    Explorer
file manager path:    (empty)
```

The script prints clear diagnostics if anything looks off. The two cases
you might see:

**Case A — `frontmost app: (empty)` and a `DIAGNOSTIC: pywin32 is not
installed in THIS Python` block.** That means the Python in your shell
doesn't have pywin32. Fix:
```
py -m pip install pywin32
```

(`py` is the Python launcher that came with the python.org installer.
`py -m pip` is more reliable than plain `pip` because it doesn't care
whether `pip.exe` is on PATH.)

OR, just use the app's venv Python — it always has pywin32 because the
bootstrap installed it. Run:
```
"%APPDATA%\WillettBot\venv\Scripts\python.exe" platform_helpers.py
```

That one is guaranteed to work because npm start already populated it.

**Case B — `frontmost app: (empty)` with no DIAGNOSTIC.** That means
pywin32 is installed but the foreground window query returned nothing.
Usually means the foreground was the desktop or a UAC dialog. Click on
a normal app window (Notepad, Explorer) and re-run.

## B5. Record + replay test

In WillettBot (the running app from step B3):

1. Click "Record New Script."
2. Pick a hotkey or use the default (F9 to start, F10 to stop).
3. Do something tiny — open File Explorer, navigate into Downloads, click
   on a file, switch to another app.
4. **Stop the recording.** Two ways — both should work now:
   - Click the **Stop** button in the recorder UI, OR
   - Press **F10** (or whatever your end hotkey is).
5. The "Save Script" panel should appear with a filename and description
   field. Fill them in and click **Save script**.
6. Click **Replay**.

What to look for:
- ✅ The Save Script panel actually appears after Stop. (If it doesn't,
  see "Common issues" → "Stop button doesn't open Save panel".)
- ✅ Clicks land where you clicked during recording.
- ✅ The compiled JSON includes `focus_app` and `open_file` actions, not
  just raw clicks. Open the script in the editor — the visual builder
  shows them as friendly blocks.
- ✅ Replay actually opens the file you opened during recording, not a
  blind click on the old pixel coordinates.

If clicks don't fire at all, it's not a permissions thing on Windows —
more likely pyautogui couldn't import. Check the dev console (Help menu →
Toggle Developer Tools) for errors.

## B6. Working loop (every time you find / fix something)

When something breaks on Windows that you want to fix from the Mac side:

1. **On Windows:** copy the exact error from the dev console / terminal.
2. **On Mac:** open <https://claude.ai>, paste the error, ask for a fix.
3. **On Mac:** apply the fix, then commit + push to GitHub.
4. **On Windows:** `git pull` from `%USERPROFILE%\willettbot`, restart
   the app (close it, `npm start` again).
5. Re-test.

That's the loop. The Mac side stays the source of truth; the Windows side
is a copy that you keep refreshing with `git pull`.

---

# SECTION C — Building the Windows installer (optional, for distribution)

This produces a `WillettBot Setup x.y.z.exe` you can give to other people.
You don't need this for personal testing — `npm start` runs the app
directly from source.

```
npm run dist:win
```

What happens:
- electron-builder packages the app into an NSIS installer.
- It tries to publish to GitHub Releases (the same release the Mac DMG
  goes to). For that you need `GH_TOKEN` set in the environment:

```
set GH_TOKEN=ghp_yourTokenHere
npm run dist:win
```

**One thing missing:** the `prepare-python:win` script in package.json
references `scripts-build/prepare-python.ps1`, which doesn't exist yet.
That script bundles a portable Python into the installer (the Windows
equivalent of `prepare-python.sh`). Without it, `dist:win` will fail at
that step. Two options:

1. **Skip Python bundling for now** — edit `package.json`, change the
   `dist:win` script to drop the `prepare-python:win &&` part. The
   resulting installer won't include Python, so users will need Python
   installed on their own machines. Fine for sharing with developers,
   not fine for "Mom" users.

2. **Write the script** — open <https://claude.ai>, paste the contents
   of `scripts-build/prepare-python.sh` (the Mac one), and ask Claude to
   write the Windows PowerShell equivalent. It needs to download a
   Windows-embeddable Python from <https://www.python.org/downloads/windows/>
   (the "embeddable package"), unpack it into `bundled-python\python\`,
   and pip-install pyautogui + pynput + pywin32 into it.

For first-time testing, Option 1 is fine.

**Code signing on Windows:** without an EV code-signing certificate
(~$300/year), Windows shows a SmartScreen "Unknown publisher" warning
when users run the installer. Users can click "More info" → "Run anyway"
to proceed. That's acceptable for beta testing; for a real launch, get
the EV cert.

---

# Common issues

**"npm start" hangs at "creating venv"**
The system Python is missing or not on PATH. Open Command Prompt and run
`python --version` — if it errors out, redo Step A1c (and make sure to
check "Add python.exe to PATH").

**"pywin32 import failed" in the dev console**
The venv bootstrap didn't finish cleanly. Delete `%APPDATA%\WillettBot\venv\`
and restart `npm start` to re-bootstrap.

**`platform_helpers.py` smoke test prints `frontmost app: (empty)`**
See Step B4 above — this is almost always pywin32 missing in the shell's
Python. Fix with `py -m pip install pywin32` or run the smoke test through
the venv Python.

**Stop button doesn't open the Save Script panel**
This was a Windows-only bug in early builds. The fix is in the repo — make
sure you've done `git pull` (Step B2) since 2026-04-27. If you're somehow
on an old build, the workaround is to press your **end hotkey** (default
F10) instead of clicking Stop. The hotkey path runs entirely inside the
Python recorder and finalizes cleanly without involving the parent process.

**Clicks "happen" but nothing visibly moves**
On Windows this almost never happens (no Accessibility-style permission
gate). If it does, the most likely cause is the cursor sitting in a screen
corner with pyautogui's FAILSAFE detection triggered. pyautogui treats the
top-left pixel as "stop" — move the cursor away and try again.

**"GetForegroundWindow() returns 0"**
The foreground window is the desktop or a system-protected app (UAC
prompt, login screen, etc.). Click on a normal app window first.

**Recorded script saves but replay does nothing**
Open the Help menu → Toggle Developer Tools → Console tab. Errors during
spawn of the Python runner show up there with a real stack trace.

**`git pull` says "Your local changes would be overwritten"**
You edited a tracked file on Windows and didn't commit it. Either commit:
```
git add <file>
git commit -m "windows-side change"
git pull
```
…or stash and re-pop:
```
git stash
git pull
git stash pop
```

---

# When you're stuck

Open <https://claude.ai> in Edge, paste:
1. Which step you were on (e.g. "Section B step B4").
2. The exact error message or screenshot.
3. The output of `python --version` and `node --version`.

I can help debug from there even without file access on the Windows side.
