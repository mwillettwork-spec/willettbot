# WillettBot — Windows Setup & Test Guide

This is the playbook for getting WillettBot running on a Windows machine
(Bootcamp, VM, or dedicated Windows PC). Follow it in order. Each step has
a "verify" check so you know it worked before moving on.

If something blows up partway through, open <https://claude.ai> in Edge and
paste the error — same Claude as on the Mac side, just no file access.

---

## 1. Install prerequisites

You need three things on Windows that you already have on Mac. Install them
in this order. Total time: ~15 minutes including downloads.

### 1a. Git for Windows
Download: <https://git-scm.com/download/win>
- Run the installer with all defaults.
- During install: when it asks about "Adjusting your PATH environment,"
  pick **"Git from the command line and also from 3rd-party software"**.

**Verify:** open Command Prompt (Win+R → `cmd`) and run:
```
git --version
```
Should print something like `git version 2.45.x`.

### 1b. Node.js (LTS, version 20 or higher)
Download: <https://nodejs.org/en/download>
- Pick the **LTS** "Windows Installer (.msi)" for x64.
- Run with all defaults — leave "Add to PATH" checked, leave the optional
  Chocolatey/Python add-on **unchecked** (we'll install Python separately
  to control the version).

**Verify:**
```
node --version
npm --version
```
Both should print versions. Node ≥ 20, npm ≥ 10.

### 1c. Python 3.12 (the python.org installer, NOT the Microsoft Store version)

⚠️ The Microsoft Store version of Python has registry shenanigans that break
pywin32 — we specifically want the python.org installer.

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

---

## 2. Clone the repo

You'll need a GitHub Personal Access Token (PAT) since the repo is private.
You probably already have one set up on the Mac side — find it in your
password manager, or generate a new one at
<https://github.com/settings/tokens> (classic, with `repo` scope).

In Command Prompt:
```
cd %USERPROFILE%
git clone https://github.com/mwillettwork-spec/willettbot.git
cd willettbot
```

When git prompts for credentials, use your GitHub username + the PAT as the
password. Windows will remember it after the first clone.

**Verify:** you should see all the source files:
```
dir
```
Look for `main.js`, `recorder.py`, `runner.py`, `platform_helpers.py`,
`platform_win.py`, etc.

---

## 3. Install Node + Python dependencies

```
npm install
```

This pulls Electron and electron-builder. Takes 1–3 minutes. There may be
warnings about deprecated transitive deps — those are harmless.

**Verify:** `node_modules\` directory exists and has hundreds of subfolders.

---

## 4. Run in dev mode

```
npm start
```

What happens:
1. Electron launches WillettBot.
2. The app sees no bundled Python, so it bootstraps a venv at
   `%APPDATA%\WillettBot\venv\` and pip-installs `pyautogui`, `pynput`,
   and `pywin32` into it. **First launch takes ~30 seconds.**
3. The hub UI appears.

If the venv bootstrap fails, you'll see an error banner. The most common
cause is python not being on PATH — re-check Step 1c.

---

## 5. Smoke-test the platform layer

Before testing record + replay, confirm the Windows backend is alive. Open
a new Command Prompt (leave the app running) and:

```
cd %USERPROFILE%\willettbot
python -c "import platform_helpers as p; print(p.PLATFORM_NAME); print(p.get_frontmost_app()); print(p.get_file_manager_name())"
```

Expected output:
```
Windows
Cmd      (or whatever app is in front)
Explorer
```

If `get_frontmost_app()` returns an empty string, pywin32 isn't installed
in your system Python. Install it:
```
pip install pywin32
```

(The bundled venv inside the app is separate — that one already got pywin32
during the bootstrap. This system-Python install is just so the standalone
`python -c` smoke test works.)

---

## 6. Record + replay test

In WillettBot:

1. Click "Record New Script."
2. Pick a hotkey or use the default.
3. Do something tiny — open File Explorer, navigate into Downloads, click
   on a file, switch to another app, stop recording.
4. Save it with a name.
5. Click Replay.

What to look for:
- ✅ Clicks land where you clicked during recording.
- ✅ The compiled JSON includes `focus_app` and `open_file` actions, not
  just raw clicks. (Open the script in the editor to see — the visual
  builder shows them as friendly blocks.)
- ✅ Replay actually opens the file you opened during recording, not a
  blind click on the old pixel coordinates.

If clicks don't fire at all, it's not a permissions thing on Windows —
more likely pyautogui couldn't import. Check the dev console (Help menu →
Toggle Developer Tools) for errors.

---

## 7. Build the Windows installer (optional, for distribution)

This produces a `WillettBot Setup x.y.z.exe` you can give to other people.
You don't need this for personal testing.

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
   write the Windows PowerShell equivalent. It'll need to download a
   Windows-embeddable Python from <https://www.python.org/downloads/windows/>
   (the "embeddable package"), unpack it into `bundled-python\python\`,
   and pip-install pyautogui + pynput + pywin32 into it.

For first-time testing, Option 1 is fine.

**Code signing on Windows:** without an EV code-signing certificate
(~$300/year), Windows will show a SmartScreen "Unknown publisher"
warning when users run the installer. Users can click "More info" →
"Run anyway" to proceed. That's acceptable for beta testing; for a real
launch, get the EV cert.

---

## Common issues

**"npm start" hangs at "creating venv"**
The system Python is missing or not on PATH. Open Command Prompt and run
`python --version` — if it errors out, redo Step 1c (and make sure to
check "Add python.exe to PATH").

**"pywin32 import failed" in the dev console**
The venv bootstrap didn't finish. Delete `%APPDATA%\WillettBot\venv\`
and restart `npm start` to re-bootstrap.

**Clicks "happen" but nothing visibly moves**
On Windows this almost never happens (no Accessibility-style permission
gate). If it does, the most likely cause is the cursor is somewhere with
the FAILSAFE corner detection triggered. pyautogui treats the top-left
pixel as "stop" — move the cursor away and try again.

**"GetForegroundWindow() returns 0"**
The foreground window is the desktop or a system-protected app (UAC
prompt, login screen, etc.). Click on a normal app window first.

**Recorded script saves but replay does nothing**
Open the Help menu → Toggle Developer Tools → Console tab. Errors during
spawn of the Python runner show up there with a real stack trace.

---

## When you're stuck

Open <https://claude.ai> in Edge, paste:
1. What you were trying to do (one line).
2. The exact error message or screenshot.
3. The output of `python --version` and `node --version`.

I can help debug from there even without file access.
