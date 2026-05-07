# Third-Party Notices

WillettBot is a proprietary application copyright © 2026 WillettBot Inc.,
a New York corporation. Its own source code is licensed only as described
in `TERMS.md` and the copyright headers on each source file.

WillettBot is built on top of, and distributes, several open-source
components. Those components remain under their original licenses and are
the property of their respective owners. This file lists the components
shipped inside the WillettBot application bundle and their licenses, in
fulfillment of those licenses' attribution and notice requirements.

If you have questions about anything below, contact:

**WillettBot Inc.**
66 Ulster Ave
Atlantic Beach, NY 11509
**mwillettwork@gmail.com**

---

## Application Framework

### Electron
- **Used for:** the desktop app shell, window management, IPC, and packaging.
- **License:** MIT License
- **Copyright:** © 2014–present GitHub Inc., © 2013–2014 Adam Roben, © Electron contributors
- **Source:** <https://github.com/electron/electron>

### electron-builder
- **Used for:** building, signing, and notarizing the macOS DMG. Build-time only — not shipped inside the application bundle.
- **License:** MIT License
- **Source:** <https://github.com/electron-userland/electron-builder>

### electron-updater
- **Used for:** auto-update checks against GitHub Releases.
- **License:** MIT License
- **Source:** <https://github.com/electron-userland/electron-builder/tree/master/packages/electron-updater>

### Node.js standard library and runtime
- **Used for:** main-process JavaScript execution.
- **License:** MIT License (Node.js project license)
- **Source:** <https://nodejs.org/>

### Chromium (via Electron)
- **Used for:** the renderer-process HTML/JS engine.
- **License:** BSD 3-Clause License (with additional notices for component
  third-party libraries; see Chromium's LICENSE manifest at
  <https://chromium.googlesource.com/chromium/src/+/main/LICENSE>)
- **Copyright:** © The Chromium Authors

---

## Python Runtime

### CPython
- **Version bundled:** 3.12 (via `python-build-standalone`).
- **Used for:** running the recorder and runner subprocesses.
- **License:** Python Software Foundation License (PSF License Agreement v2)
- **Copyright:** © 2001–present Python Software Foundation
- **Source:** <https://www.python.org/>

### python-build-standalone
- **Used for:** the portable, self-contained Python distribution shipped inside the app.
- **License:** Mozilla Public License 2.0 (project tooling) and PSF License (Python itself).
- **Source:** <https://github.com/astral-sh/python-build-standalone>

---

## Python Libraries (bundled inside the app)

### pyautogui
- **Used for:** simulating mouse and keyboard input during script replay.
- **License:** BSD 3-Clause License
- **Copyright:** © 2014 Al Sweigart
- **Source:** <https://github.com/asweigart/pyautogui>

### pynput
- **Used for:** capturing mouse and keyboard input during script recording.
- **License:** **GNU Lesser General Public License v3.0 (LGPL-3.0)**
- **Copyright:** © Moses Palmér
- **Source:** <https://github.com/moses-palmer/pynput>
- **LGPL notice and relinkability statement:** The unmodified pynput source
  code is included inside the WillettBot application bundle at
  `Contents/Resources/python/lib/python3.12/site-packages/pynput/`. End users
  may replace those files with a modified version of pynput if they wish, in
  accordance with Section 4 of the LGPL-3.0. The full text of the LGPL-3.0
  is available at <https://www.gnu.org/licenses/lgpl-3.0.html>. WillettBot
  uses pynput as an unmodified dynamic dependency loaded via Python's
  `import` mechanism; no static linking is performed.

### pyautogui's transitive dependencies
- **MouseInfo** — BSD 3-Clause — © Al Sweigart — <https://github.com/asweigart/mouseinfo>
- **PyGetWindow** — BSD 3-Clause — © Al Sweigart — <https://github.com/asweigart/pygetwindow>
- **PyRect** — BSD 3-Clause — © Al Sweigart — <https://github.com/asweigart/pyrect>
- **PyScreeze** — MIT License — © Al Sweigart — <https://github.com/asweigart/pyscreeze>
- **PyTweening** — MIT License — © Al Sweigart — <https://github.com/asweigart/pytweening>
- **PyMsgBox** — BSD 3-Clause — © Al Sweigart — <https://github.com/asweigart/pymsgbox>
- **Pillow** (PIL fork) — MIT-CMU License — © Jeffrey A. Clark and contributors — <https://github.com/python-pillow/Pillow>
- **pyobjc** (macOS only) — MIT License — © Ronald Oussoren et al. — <https://github.com/ronaldoussoren/pyobjc>

### pynput's transitive dependencies
- **pyobjc** — MIT License — see above
- **six** — MIT License — © Benjamin Peterson — <https://github.com/benjaminp/six>

---

## License Texts

The full license texts of each component are available at the URLs listed
above and (for the libraries bundled with the application) inside their
respective package directories under
`Contents/Resources/python/lib/python3.12/site-packages/`.

For Electron and electron-updater, full license texts are available inside
the application bundle at `Contents/Resources/app.asar.unpacked/node_modules/<package>/LICENSE`.

---

## What WillettBot Itself Is Not Licensing to You

The WillettBot application code (everything outside the `node_modules/`
and `bundled-python/` directories) is proprietary and is **not** distributed
under any of the licenses above. Use of the WillettBot application is
governed solely by `TERMS.md` and your active subscription on
willettbot.com.

---

*Last updated: May 6, 2026*
