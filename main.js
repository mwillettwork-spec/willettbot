// Copyright (c) 2026 Myles Willett. All rights reserved.
// Proprietary and confidential. No reproduction, distribution, or use
// without express written permission.

const { app, BrowserWindow, ipcMain, shell, dialog, screen, globalShortcut } = require('electron')
const path = require('path')
const { exec, spawn } = require('child_process')
const fs = require('fs')
const os = require('os')

// electron-updater = check GitHub Releases for new DMGs on launch, download
// them in the background, and prompt the user to restart. Wrapped in a
// try/require so `npm start` in dev still works before the dep is installed.
let autoUpdater = null
try {
  autoUpdater = require('electron-updater').autoUpdater
} catch (e) {
  console.log('[auto-update] electron-updater not installed — dev mode ok')
}

// ── AUTO-UPDATE WIRING ──────────────────────────────────────────────────────
// Checks on every launch when the app is packaged (not in `electron .` dev).
// Silent until a new version has finished downloading — then we pop a dialog
// asking the user to restart. Failure is logged but never blocks the app
// (offline Mac, GitHub down, rate limits — none of that should stall launch).
function setupAutoUpdate() {
  if (!autoUpdater) return
  if (!app.isPackaged) { console.log('[auto-update] skipped — dev mode'); return }

  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('checking-for-update', () => console.log('[auto-update] checking…'))
  autoUpdater.on('update-available',    (info) => console.log('[auto-update] available:', info.version))
  autoUpdater.on('update-not-available',(info) => console.log('[auto-update] up to date:', info.version))
  autoUpdater.on('error',               (err)  => console.error('[auto-update] error:', err && err.message))

  autoUpdater.on('download-progress', (p) => {
    // Keep this at log-level — prompting the user mid-download would be
    // annoying. They'll see the "restart to update" dialog when it finishes.
    console.log('[auto-update] download', Math.round(p.percent) + '%')
  })

  autoUpdater.on('update-downloaded', (info) => {
    console.log('[auto-update] ready:', info.version)
    // Non-blocking dialog. If the user clicks "Later", electron-updater's
    // autoInstallOnAppQuit will apply the update next time the app quits.
    dialog.showMessageBox({
      type: 'info',
      buttons: ['Restart now', 'Later'],
      defaultId: 0,
      cancelId: 1,
      title: 'WillettBot update ready',
      message: 'Version ' + info.version + ' has been downloaded.',
      detail: 'Restart to finish applying the update. Your recorded scripts and activation will be preserved.'
    }).then(({ response }) => {
      if (response === 0) autoUpdater.quitAndInstall()
    }).catch(e => console.error('[auto-update dialog]', e))
  })

  // Fire the check. 10-sec delay to avoid fighting Python bootstrap for CPU
  // / network at launch; the user won't notice either way since updates
  // download in the background.
  setTimeout(() => {
    autoUpdater.checkForUpdates().catch(e => console.error('[auto-update check]', e))
  }, 10000)
}

// Hold the currently running clicker child process (if any) so we can kill it.
let clickerProc = null
// Hold the currently running script runner child (if any).
let scriptProc = null
// Hold the currently running recorder (pynput-based macro capture).
let recorderProc = null

// Global emergency-stop Esc hotkey. Because scripts run the mouse/keyboard at
// machine speed, we want a single reliable panic button the user can hit from
// anywhere on their Mac — even while the script is dragging focus around. We
// register Escape as a globalShortcut only while a script or clicker is
// running, and unregister as soon as nothing is active so normal Esc behavior
// (closing dialogs, stopping playback, etc.) is unaffected the rest of the
// time.
let _stopHotkeyRegistered = false
function installStopHotkey() {
  if (_stopHotkeyRegistered) return
  try {
    const ok = globalShortcut.register('Escape', () => {
      // Kill anything that's running. Use SIGTERM so the Python side can
      // still emit a clean 'stopped' event before exiting.
      if (scriptProc) {
        try { scriptProc.kill('SIGTERM') } catch (e) {}
        // Notify the UI so the Stop button resets even if the user pressed
        // Esc instead of clicking it.
        try { BrowserWindow.getAllWindows().forEach(w => w.webContents.send('script-event', { event: 'stopped', reason: 'hotkey' })) } catch (e) {}
      }
      if (clickerProc) {
        try { clickerProc.kill('SIGTERM') } catch (e) {}
      }
      if (recorderProc) {
        try { recorderProc.kill('SIGTERM') } catch (e) {}
      }
      uninstallStopHotkey()
    })
    _stopHotkeyRegistered = !!ok
  } catch (e) { console.error('[stop-hotkey register]', e) }
}
function uninstallStopHotkey() {
  if (!_stopHotkeyRegistered) return
  try { globalShortcut.unregister('Escape') } catch (e) {}
  _stopHotkeyRegistered = false
}

// Where user-writable data lives. Critical once the app is packaged: the
// .app bundle itself is read-only on macOS, so scripts / favorites / schedules
// MUST live in the per-user data directory. Can be overridden with an env var
// for tests. Falls back to __dirname for bare-node contexts (which is how the
// test harnesses load main.js via _compile).
const DATA_DIR = process.env.WILLETTBOT_DATA_DIR ||
  ((app && typeof app.getPath === 'function')
    ? (() => { try { return app.getPath('userData') } catch (e) { return __dirname } })()
    : __dirname)

// Where bundled Python scripts live at runtime. In development this is just
// __dirname. Once packaged, __dirname points inside app.asar — and Python
// (a separate process) can't read inside an asar archive. electron-builder is
// configured to unpack *.py alongside app.asar at app.asar.unpacked/, so at
// runtime we swap the path to point there.
const PY_DIR = __dirname.includes(`app.asar${path.sep}`) || __dirname.endsWith('app.asar')
  ? __dirname.replace(/app\.asar(?=$|[\\/])/, 'app.asar.unpacked')
  : __dirname

// ── Python runtime bootstrap ─────────────────────────────────────────
// WillettBot's automation scripts need pyautogui + pynput at runtime.
// Two paths:
//
//   1. PACKAGED (production): we ship a portable Python with all deps
//      pre-installed inside the .app bundle at Contents/Resources/python/.
//      Zero setup needed — the app just uses that Python directly.
//
//   2. DEV MODE (`npm start`): no bundled Python on disk. Fall back to
//      creating a venv in DATA_DIR the first time and pip-install the
//      deps from the user's system Python. This is how the dev loop
//      works without requiring Myles to run prepare-python.sh.

// PACKAGED path: extraResources in package.json copies bundled-python/python/
// to <app>.app/Contents/Resources/python/. process.resourcesPath resolves to
// that Contents/Resources dir at runtime. In dev this points at Electron's
// own resources which won't contain our Python — so the lookup fails and
// we fall through to the venv bootstrap below.
const BUNDLED_PY = (() => {
  try {
    if (!app || !app.isPackaged || !process.resourcesPath) return null
    const p = path.join(process.resourcesPath, 'python', 'bin', 'python3')
    return fs.existsSync(p) ? p : null
  } catch (_) { return null }
})()

const VENV_DIR   = path.join(DATA_DIR, 'venv')
const VENV_PY    = path.join(VENV_DIR, 'bin', 'python3')
const VENV_READY = path.join(VENV_DIR, '.wb-ready')   // marker: setup succeeded
const PY_DEPS    = ['pyautogui', 'pynput']

// Search common macOS install locations for a system python3 to bootstrap
// the venv. We only need this once — the venv has its own python after.
function findBootstrapPython() {
  const candidates = [
    '/opt/homebrew/bin/python3',                                 // Apple Silicon Homebrew
    '/usr/local/bin/python3',                                    // Intel Homebrew / python.org
    '/Library/Frameworks/Python.framework/Versions/Current/bin/python3', // python.org installer
    '/usr/bin/python3',                                          // older system python
    '/Library/Developer/CommandLineTools/usr/bin/python3'        // Xcode Command Line Tools
  ]
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p } catch (_) {}
  }
  return 'python3'  // last resort: hope PATH has it
}

function pythonEnvReady() {
  try { return fs.existsSync(VENV_PY) && fs.existsSync(VENV_READY) }
  catch (_) { return false }
}

// Broadcast bootstrap progress so the hub can show a "setting up" banner.
function broadcastPyBootstrap(stage, detail) {
  const wins = BrowserWindow.getAllWindows()
  for (const w of wins) {
    try { w.webContents.send('py-bootstrap', { stage, detail: detail || null }) }
    catch (_) {}
  }
}

// Promise cache so concurrent callers share one bootstrap, and we don't
// try to re-run it if it already succeeded this session.
let pythonEnvPromise = null
function ensurePythonEnv() {
  // Fast path — packaged app: use the Python we ship inside the .app.
  // This is the ONLY path regular users ever hit, so it must cost nothing.
  if (BUNDLED_PY) return Promise.resolve(BUNDLED_PY)
  if (pythonEnvReady()) return Promise.resolve(VENV_PY)
  if (pythonEnvPromise)  return pythonEnvPromise
  pythonEnvPromise = (async () => {
    const bootstrap = findBootstrapPython()
    broadcastPyBootstrap('creating-venv')
    await new Promise((resolve, reject) => {
      const p = spawn(bootstrap, ['-m', 'venv', VENV_DIR], { stdio: 'pipe' })
      let err = ''
      p.stderr.on('data', d => { err += d.toString() })
      p.on('error', reject)
      p.on('close', code => code === 0 ? resolve()
        : reject(new Error('venv creation failed (code ' + code + '): ' + err.trim())))
    })
    broadcastPyBootstrap('installing-deps')
    await new Promise((resolve, reject) => {
      const p = spawn(VENV_PY,
        ['-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', ...PY_DEPS],
        { stdio: 'pipe' })
      let err = ''
      p.stderr.on('data', d => { err += d.toString() })
      p.on('error', reject)
      p.on('close', code => code === 0 ? resolve()
        : reject(new Error('pip install failed (code ' + code + '): ' + err.trim())))
    })
    fs.writeFileSync(VENV_READY, new Date().toISOString())
    broadcastPyBootstrap('ready')
    return VENV_PY
  })().catch(err => {
    broadcastPyBootstrap('failed', String(err && err.message || err))
    pythonEnvPromise = null  // clear cache so next call retries
    throw err
  })
  return pythonEnvPromise
}

// Where user scripts live. Bootstrapped on startup if missing. First launch
// after install seeds from the bundled `scripts-seed/` extra-resources dir
// so new users have a few example scripts to browse.
const SCRIPTS_DIR = path.join(DATA_DIR, 'scripts')

// Where favorite-flags live. Just a list of filenames the user has ♥'d so they
// bubble up to the hub as quick-launch cards. Kept separate from each script
// file so favoriting doesn't rewrite script JSON.
const FAVORITES_FILE = path.join(DATA_DIR, 'favorites.json')

function loadFavorites() {
  try {
    if (!fs.existsSync(FAVORITES_FILE)) return []
    const raw = fs.readFileSync(FAVORITES_FILE, 'utf8')
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // Deduplicate + drop non-strings just in case the file got hand-edited.
    return Array.from(new Set(parsed.filter(f => typeof f === 'string')))
  } catch (e) {
    console.error('[willettbot] favorites load failed:', e)
    return []
  }
}

function saveFavorites(list) {
  try {
    fs.writeFileSync(FAVORITES_FILE,
                     JSON.stringify(Array.from(new Set(list)), null, 2), 'utf8')
    return true
  } catch (e) {
    console.error('[willettbot] favorites save failed:', e)
    return false
  }
}

// Drop any favorite whose underlying script file no longer exists — keeps the
// hub from rendering dead cards after a user deletes a script from disk.
function pruneFavorites(list) {
  return list.filter(f => {
    try { return fs.existsSync(path.join(SCRIPTS_DIR, f)) }
    catch (e) { return false }
  })
}

// ── ACTIVATION / LICENSE ───────────────────────────────────────────────────
// Ed25519-signed keys minted by licensing/keygen.js. The public key is bundled
// with the app; there's no network call — the whole thing validates locally.
// Activation state lives in the per-user Electron userData directory so it
// survives across app sessions but can't accidentally land in the repo.
const { verifyKey: verifyLicenseKey, PREFIX: LICENSE_PREFIX } =
  require('./licensing/keygen.js')

const PUB_KEY_PATH = path.join(__dirname, 'licensing', 'public_key.pem')
let PUB_KEY_PEM = null
try {
  PUB_KEY_PEM = fs.readFileSync(PUB_KEY_PATH, 'utf8')
} catch (e) {
  console.error('[willettbot] public key missing at', PUB_KEY_PATH,
                '— activation will always fail until you run',
                '`node licensing/keygen.js init`.')
}

// Lazily resolved because app.getPath('userData') isn't available until
// whenReady. We cache after first call.
let _activationFilePath = null
function activationFilePath() {
  if (_activationFilePath) return _activationFilePath
  try {
    _activationFilePath = path.join(app.getPath('userData'), 'activation.json')
  } catch (e) {
    // Fallback for off-Electron test harnesses — use DATA_DIR which also
    // honors WILLETTBOT_DATA_DIR so tests can point at a scratch folder.
    _activationFilePath = path.join(DATA_DIR, 'activation.json')
  }
  return _activationFilePath
}

function loadActivation() {
  try {
    const p = activationFilePath()
    if (!fs.existsSync(p)) return null
    const raw = JSON.parse(fs.readFileSync(p, 'utf8'))
    if (!raw || typeof raw !== 'object' || typeof raw.key !== 'string') return null
    return raw
  } catch (e) {
    console.error('[willettbot] activation load failed:', e)
    return null
  }
}

function saveActivation(record) {
  try {
    const p = activationFilePath()
    fs.mkdirSync(path.dirname(p), { recursive: true })
    fs.writeFileSync(p, JSON.stringify(record, null, 2), 'utf8')
    return true
  } catch (e) {
    console.error('[willettbot] activation save failed:', e)
    return false
  }
}

// Quick read: is the stored key still valid right now?
function currentActivationState() {
  if (!PUB_KEY_PEM) {
    return { activated: false, reason: 'no public key bundled' }
  }
  const rec = loadActivation()
  if (!rec) return { activated: false }
  const res = verifyLicenseKey(rec.key, PUB_KEY_PEM)
  if (!res.ok) {
    return { activated: false, reason: res.reason, expired: !!res.expired,
             info: res.payload || null, key: rec.key }
  }
  return { activated: true, info: res.payload, key: rec.key,
           activatedAt: rec.activatedAt || null }
}

// Track the main window so we can surface native dialogs & refocus it.
let mainWin = null

function createWindow() {
  mainWin = new BrowserWindow({
    width: 1000,
    height: 720,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  })
  mainWin.loadFile('hub.html')
  mainWin.on('closed', () => { mainWin = null })
}

app.whenReady().then(() => {
  createWindow()
  // Kick off the Python env setup in the background so it's ready
  // by the time the user tries to run or record something. Errors
  // are surfaced via the py-bootstrap IPC channel to the hub.
  ensurePythonEnv().catch(err => {
    console.error('[ensurePythonEnv] background bootstrap failed:', err)
  })
  // Fire the auto-update check. Safe to call — guards internally against
  // dev mode and missing dependency. Never blocks window creation.
  try { setupAutoUpdate() } catch (e) { console.error('[setupAutoUpdate]', e) }
})

// Never leave a runaway clicker or script behind when the app quits.
app.on('before-quit', () => {
  if (clickerProc) {
    try { clickerProc.kill('SIGTERM') } catch (e) {}
    clickerProc = null
  }
  if (scriptProc) {
    try { scriptProc.kill('SIGTERM') } catch (e) {}
    scriptProc = null
  }
  if (recorderProc) {
    try { recorderProc.kill('SIGTERM') } catch (e) {}
    recorderProc = null
  }
  // Release any global shortcut we grabbed while a script/clicker was running
  // so Esc doesn't stay captured by our (dead) handler.
  try { globalShortcut.unregisterAll() } catch (e) {}
})

// Make sure the scripts folder exists on startup and keep its seed example
// scripts in sync with whatever's bundled in this build.
//
// First install: SCRIPTS_DIR doesn't exist → mkdir + copy every seed file.
// Upgrade:       SCRIPTS_DIR exists → for each seed file, if the on-disk copy
//                still matches the hash we recorded the last time we seeded
//                it, the user hasn't touched it and we safely overwrite with
//                the new bundled version. If the hash differs, the user has
//                customized it — leave it alone. New seed files (that didn't
//                exist in the previous bundle) are always added.
//
// The manifest (seed-manifest.json) maps seed filename → sha256 of the
// version last written by the seeder. Lets upgrades push bug-fixes to example
// scripts without clobbering user edits. Stored OUTSIDE SCRIPTS_DIR so it
// never shows up as a script in the list.
try {
  const crypto = require('crypto')
  const manifestPath = path.join(DATA_DIR, 'seed-manifest.json')
  // Clean up the old manifest location (dot-prefixed inside SCRIPTS_DIR) from
  // previous builds that wrote it there — it confused the script list.
  try {
    const oldPath = path.join(SCRIPTS_DIR, '.seed-manifest.json')
    if (fs.existsSync(oldPath)) fs.unlinkSync(oldPath)
  } catch (e) {}
  if (!fs.existsSync(SCRIPTS_DIR)) fs.mkdirSync(SCRIPTS_DIR, { recursive: true })

  // One-shot cleanup: remove seed scripts we no longer ship. Currently only
  // copy.json, which was a dev-testing scratch file that leaked into 1.0.2.
  // Only remove if contents match the known shipped version (don't nuke
  // anything the user might have kept deliberately). The manifest is checked
  // here in the upgrade branch too, so if they've modified it we leave it.
  const RETIRED_SEEDS = ['copy.json']

  // In a packaged app this resolves to
  //   <app>.app/Contents/Resources/scripts-seed
  // via extraResources. In dev (`npm start`) process.resourcesPath points at
  // Electron's internal resources, so we fall back to __dirname/scripts.
  const candidates = []
  if (process.resourcesPath) {
    candidates.push(path.join(process.resourcesPath, 'scripts-seed'))
  }
  candidates.push(path.join(__dirname, 'scripts'))
  let seedDir = null
  for (const src of candidates) {
    try {
      if (fs.existsSync(src) && fs.statSync(src).isDirectory()) {
        seedDir = src
        break
      }
    } catch (e) { /* try next */ }
  }

  if (seedDir) {
    const sha = (buf) => crypto.createHash('sha256').update(buf).digest('hex')
    let manifest = {}
    try {
      if (fs.existsSync(manifestPath)) {
        manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')) || {}
      }
    } catch (e) { manifest = {} }

    // Clean up retired seed scripts: if the user's on-disk copy still matches
    // the last hash we seeded it with, they haven't modified it, so it's safe
    // to remove. Anything modified is preserved.
    for (const retired of RETIRED_SEEDS) {
      try {
        const p = path.join(SCRIPTS_DIR, retired)
        if (!fs.existsSync(p)) continue
        const prev = manifest[retired]
        const cur = sha(fs.readFileSync(p))
        if (prev && prev === cur) fs.unlinkSync(p)
      } catch (e) { /* leave alone on any error */ }
    }

    const newManifest = {}
    for (const f of fs.readdirSync(seedDir)) {
      if (!f.endsWith('.json')) continue
      const from = path.join(seedDir, f)
      const to   = path.join(SCRIPTS_DIR, f)
      let fromBuf
      try { fromBuf = fs.readFileSync(from) } catch (e) { continue }
      const fromHash = sha(fromBuf)
      newManifest[f] = fromHash

      if (!fs.existsSync(to)) {
        // New seed, first install OR new script added in this build.
        try { fs.writeFileSync(to, fromBuf) } catch (e) {}
        continue
      }
      // Upgrade path: overwrite only if the user's copy matches the last
      // version we seeded (i.e. they haven't modified it). This lets us ship
      // fixes to example scripts without nuking user customizations.
      const prevHash = manifest[f]
      let currentRaw = null
      let currentHash = null
      try {
        currentRaw = fs.readFileSync(to, 'utf8')
        currentHash = sha(Buffer.from(currentRaw, 'utf8'))
      } catch (e) {}

      let shouldUpgrade = false
      if (prevHash && currentHash && prevHash === currentHash && currentHash !== fromHash) {
        shouldUpgrade = true
      }
      // Legacy migration: users who installed before the manifest existed have
      // no prevHash for their seed files. For known-broken old seed versions
      // we detect structural markers in the content and force-upgrade. Right
      // now the only one is send_gmail.json's old Chrome AppleScript flow,
      // which hangs on `wait_for_app_frontmost` + hardcoded `tell application
      // "Google Chrome"`. The new version uses `open <mailto/compose url>` via
      // the default browser and has neither marker.
      if (!shouldUpgrade && !prevHash && currentRaw && f === 'send_gmail.json') {
        const looksOld =
          currentRaw.indexOf('wait_for_app_frontmost') >= 0 ||
          currentRaw.indexOf('Google Chrome') >= 0
        if (looksOld && currentHash !== fromHash) shouldUpgrade = true
      }

      if (shouldUpgrade) {
        try { fs.writeFileSync(to, fromBuf) } catch (e) {}
      }
    }
    try { fs.writeFileSync(manifestPath, JSON.stringify(newManifest, null, 2)) } catch (e) {}
  }
} catch (e) { console.error('[willettbot] could not create scripts dir:', e) }

// ── TOAST NOTIFICATION ──
// A frameless, transparent, always-on-top BrowserWindow anchored to the
// top-right of the primary display. Used for confirm prompts — it looks &
// feels like a native macOS notification but has our own Continue / Cancel
// buttons. Returns a Promise resolving to true (continue) or false (cancel).
let activeToast = null
let activeToastResolve = null

function showToast(opts) {
  // Accept either a raw string or an options object for backwards-compat.
  if (typeof opts === 'string') opts = { message: opts }
  opts = opts || {}

  // If another toast is somehow still open, resolve it as cancelled first.
  if (activeToast) {
    try { activeToast.close() } catch (e) {}
    if (activeToastResolve) activeToastResolve(false)
    activeToast = null
    activeToastResolve = null
  }

  const display = screen.getPrimaryDisplay()
  const { width } = display.workArea
  const { x: workX, y: workY } = display.workArea

  const W = 360
  const H = 140
  const margin = 12

  const toastWin = new BrowserWindow({
    width: W,
    height: H,
    x: workX + width - W - margin,
    y: workY + margin,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,      // our CSS does the shadow
    show: false,           // show after we finish loading to avoid a flash
    backgroundColor: '#00000000',
    vibrancy: 'popover',   // optional macOS vibrancy fallback under the CSS blur
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  })
  // Float above fullscreen apps, match native notification behavior.
  try { toastWin.setAlwaysOnTop(true, 'screen-saver') } catch (e) {}
  try { toastWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true }) } catch (e) {}

  toastWin.loadFile('toast.html')
  toastWin.once('ready-to-show', () => {
    toastWin.showInactive()   // no focus-steal on first render; keyboard is bound after
    toastWin.webContents.send('toast-init', {
      message: String(opts.message || ''),
      confirmLabel: opts.confirmLabel || null,
      cancelLabel: opts.cancelLabel || null
    })
    // Give the toast focus so Enter/Esc keyboard shortcuts work.
    try { toastWin.focus() } catch (e) {}
  })

  activeToast = toastWin
  return new Promise((resolve) => {
    activeToastResolve = resolve
    toastWin.on('closed', () => {
      if (activeToast === toastWin) {
        // If the window was closed without a button click (e.g. user killed it),
        // treat it as a cancel so the runner doesn't hang forever.
        if (activeToastResolve) activeToastResolve(false)
        activeToast = null
        activeToastResolve = null
      }
    })
  })
}

// Toast's renderer sends this when the user clicks Continue / Cancel / Close.
ipcMain.on('toast-response', (event, payload) => {
  const cancelled = !!(payload && payload.cancelled)
  if (activeToastResolve) {
    activeToastResolve(!cancelled)   // resolve to true = continue, false = cancel
    activeToastResolve = null
  }
  if (activeToast) {
    try { activeToast.close() } catch (e) {}
    activeToast = null
  }
})

// Helper: extract the last line from stdout that parses as JSON with x/y keys.
// Tolerates pyautogui / pynput import-time chatter on some macOS configs.
function extractPositionFromStdout(stdout) {
  const lines = stdout.split('\n').map(l => l.trim()).filter(Boolean)
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      const parsed = JSON.parse(lines[i])
      if (parsed && typeof parsed.x === 'number' && typeof parsed.y === 'number') {
        return parsed
      }
      if (parsed && parsed.error) {
        return { error: parsed.error }
      }
    } catch (_) { /* skip non-JSON line */ }
  }
  return null
}

// ── CLICKER: one-shot — grab current mouse position ──
ipcMain.on('clicker-get-position', async (event) => {
  let py
  try { py = await ensurePythonEnv() }
  catch (e) {
    event.reply('clicker-position', { error: 'Python setup failed: ' + e.message })
    return
  }
  const scriptPath = path.join(PY_DIR, 'clicker.py')
  const cmd = `"${py}" "${scriptPath}" --get-position`
  console.log('[clicker-get-position] scriptPath =', scriptPath)
  console.log('[clicker-get-position] exec =', cmd)

  exec(cmd, { timeout: 10000 }, (error, stdout, stderr) => {
    console.log('[clicker-get-position] error  =', error && error.message)
    console.log('[clicker-get-position] stdout =', JSON.stringify(stdout))
    console.log('[clicker-get-position] stderr =', JSON.stringify(stderr))

    // ALWAYS include the raw output in the reply, so the UI can surface it
    // when something goes wrong instead of swallowing the real error.
    const debug = { stdout: stdout, stderr: stderr, cmd: cmd }

    if (error) {
      event.reply('clicker-position', {
        error: (stderr || error.message).trim() || 'exec error',
        _debug: debug
      })
      return
    }

    const pos = extractPositionFromStdout(stdout)
    if (pos && typeof pos.x === 'number') {
      event.reply('clicker-position', { x: pos.x, y: pos.y, _debug: debug })
    } else if (pos && pos.error) {
      event.reply('clicker-position', { error: pos.error, _debug: debug })
    } else {
      const trimmedErr = (stderr || '').trim()
      event.reply('clicker-position', {
        error: trimmedErr || ('No valid JSON in stdout. Got: ' + JSON.stringify(stdout || '')),
        _debug: debug
      })
    }
  })
})

// ── CLICKER: start the click loop ──
ipcMain.on('clicker-start', async (event, config) => {
  let py
  try { py = await ensurePythonEnv() }
  catch (e) {
    event.reply('clicker-error', { error: 'Python setup failed: ' + e.message })
    return
  }
  // If one is already running, kill it first.
  if (clickerProc) {
    try { clickerProc.kill('SIGTERM') } catch (e) {}
    clickerProc = null
  }

  const tmpFile = path.join(os.tmpdir(), 'willettbot_clicker.json')
  fs.writeFileSync(tmpFile, JSON.stringify(config), 'utf8')

  clickerProc = spawn(py, [path.join(PY_DIR, 'clicker.py'), tmpFile])
  installStopHotkey()

  let stdoutBuf = ''
  let stderrBuf = ''
  let sawTerminalEvent = false  // set true once we emit a done/error/stopped/failsafe

  // Parse line-delimited JSON events from clicker.py stdout.
  clickerProc.stdout.on('data', (chunk) => {
    stdoutBuf += chunk.toString()
    let nl
    while ((nl = stdoutBuf.indexOf('\n')) !== -1) {
      const line = stdoutBuf.slice(0, nl).trim()
      stdoutBuf = stdoutBuf.slice(nl + 1)
      if (!line) continue

      let evt
      try { evt = JSON.parse(line) } catch (e) { continue }  // ignore non-JSON noise

      if (evt.event === 'click') {
        event.reply('clicker-tick', { count: evt.count })
      } else if (evt.event === 'done') {
        let doneReason = 'finished'
        if (evt.reason === 'click-limit') doneReason = 'click limit reached'
        else if (evt.reason === 'time-limit') doneReason = 'time limit reached'
        event.reply('clicker-done', { reason: doneReason, count: evt.count })
        sawTerminalEvent = true
      } else if (evt.event === 'failsafe') {
        event.reply('clicker-done', { reason: 'stopped', count: evt.count })
        sawTerminalEvent = true
      } else if (evt.event === 'stopped') {
        const reason = evt.reason === 'hotkey' ? 'stopped by hotkey' : 'stopped'
        event.reply('clicker-done', { reason: reason, count: evt.count })
        sawTerminalEvent = true
      } else if (evt.event === 'warning') {
        event.reply('clicker-warning', { message: evt.message })
      } else if (evt.event === 'error') {
        // Pass the error code + python_path through so the hub can pop the
        // permission banner when Accessibility is the culprit. Without this
        // the user sees "Error: clicks are being silently dropped..." as
        // plain status text and has to figure out the fix on their own.
        event.reply('clicker-done', {
          reason: 'Error: ' + (evt.message || 'unknown'),
          count: evt.count || 0,
          code: evt.code || null,
          pythonPath: evt.python_path || null,
        })
        sawTerminalEvent = true
      }
    }
  })

  clickerProc.stderr.on('data', (chunk) => {
    stderrBuf += chunk.toString()
    console.error('[clicker]', chunk.toString())
  })

  clickerProc.on('close', (code) => {
    clickerProc = null
    if (!scriptProc && !recorderProc) uninstallStopHotkey()
    // Only synthesize an error if the Python side didn't already report one.
    if (!sawTerminalEvent) {
      const trimmedErr = stderrBuf.trim()
      const reason = trimmedErr
        ? ('Python error: ' + trimmedErr.split('\n').pop())
        : ('exited with code ' + code)
      event.reply('clicker-done', { reason: reason, count: 0 })
    }
  })

  clickerProc.on('error', (err) => {
    event.reply('clicker-done', { reason: 'Failed to start python3: ' + err.message, count: 0 })
    clickerProc = null
  })
})

// ── CLICKER: stop the click loop ──
ipcMain.on('clicker-stop', (event) => {
  if (clickerProc) {
    try { clickerProc.kill('SIGTERM') } catch (e) {}
    clickerProc = null
  }
})

// ── MISC: open an external URL in the user's default browser ──
ipcMain.on('open-external-url', (event, url) => {
  if (typeof url === 'string' && /^https?:\/\//.test(url)) {
    shell.openExternal(url).catch(err => console.error('[open-external]', err))
  }
})

// ── SCRIPTS: list the JSON files in scripts/ with their metadata ──
ipcMain.handle('list-scripts', async () => {
  try {
    // Filter: .json files only, no dotfile hidden files (the old seeder
    // wrote .seed-manifest.json into this dir and we never want it to
    // appear as a fake "script" in the user's list).
    const files = fs.readdirSync(SCRIPTS_DIR)
      .filter(f => f.endsWith('.json') && !f.startsWith('.'))
    // Prune + persist favorites so a deleted script disappears from the hub
    // silently on the next list refresh.
    const existingSet = new Set(files)
    const rawFavs = loadFavorites()
    const pruned = rawFavs.filter(f => existingSet.has(f))
    if (pruned.length !== rawFavs.length) saveFavorites(pruned)
    const favSet = new Set(pruned)
    return files.map(filename => {
      const full = path.join(SCRIPTS_DIR, filename)
      try {
        const raw = fs.readFileSync(full, 'utf8')
        const parsed = JSON.parse(raw)
        return {
          filename: filename,
          path: full,
          name: parsed.name || filename,
          description: parsed.description || '',
          actionCount: Array.isArray(parsed.actions) ? parsed.actions.length : 0,
          variables: parsed.variables || {},
          favorite: favSet.has(filename)
        }
      } catch (e) {
        return { filename, path: full, name: filename,
                 description: '(invalid JSON)', actionCount: 0, variables: {},
                 favorite: favSet.has(filename), error: e.message }
      }
    })
  } catch (e) {
    return { error: e.message }
  }
})

// ── PERMISSION SELF-CHECK ───────────────────────────────────────────────────
// Runs permcheck.py via the bundled Python to detect macOS Accessibility /
// Automation / Input Monitoring grants. Critical because the bundled Python
// is a distinct unsigned binary from the Electron shell — it needs its OWN
// permission grants. Without them, pyautogui.click() silently no-ops and
// osascript returns -1743, and the app appears "broken" with no error.
ipcMain.handle('check-permissions', async () => {
  try {
    const py = await ensurePythonEnv()
    return await new Promise((resolve) => {
      const scriptPath = path.join(PY_DIR, 'permcheck.py')
      exec(`"${py}" "${scriptPath}"`, { timeout: 8000 }, (err, stdout, stderr) => {
        if (err) {
          return resolve({ ok: false, error: (stderr || err.message).trim(),
                            pythonPath: py })
        }
        const last = String(stdout || '').trim().split('\n').pop() || ''
        try {
          const parsed = JSON.parse(last)
          resolve({ ok: true, ...parsed, pythonPath: parsed.python_path || py })
        } catch (e) {
          resolve({ ok: false, error: 'Could not parse permcheck output.',
                    raw: stdout, pythonPath: py })
        }
      })
    })
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

// Open a specific pane of macOS System Settings by URL scheme. Saves the
// user from hunting through Privacy & Security to find the right list.
ipcMain.on('open-system-settings', (event, pane) => {
  const URLS = {
    accessibility:    'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility',
    automation:       'x-apple.systempreferences:com.apple.preference.security?Privacy_Automation',
    inputMonitoring:  'x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent'
  }
  const url = URLS[pane] || URLS.accessibility
  shell.openExternal(url).catch(e => console.error('[open-settings]', e))
})

// Nuke every TCC grant macOS has for our bundle ID. Critical for non-technical
// users: every rebuild gives the bundled python3 a new code hash, which
// invalidates the prior Accessibility grant — pyautogui then silently no-ops
// with no new prompt. tccutil reset clears the stale record so the next click
// attempt fires a fresh permission prompt. User doesn't need to find and
// delete entries in System Settings.
ipcMain.handle('reset-tcc', async () => {
  const bundleId = 'com.willett.willettbot'
  return await new Promise((resolve) => {
    exec(`tccutil reset All ${bundleId}`, { timeout: 5000 }, (err, stdout, stderr) => {
      if (err) {
        return resolve({ ok: false, error: (stderr || err.message).trim() })
      }
      resolve({ ok: true, output: (stdout || '').trim() })
    })
  })
})

// Reveal the bundled Python binary in Finder so the user can drag it into
// the Accessibility / Input Monitoring list on macOS versions that don't
// auto-add unsigned binaries when the prompt fires (Sonoma+ is stricter).
ipcMain.on('reveal-python-binary', async () => {
  try {
    const py = await ensurePythonEnv()
    shell.showItemInFolder(py)
  } catch (e) {
    console.error('[reveal-python]', e)
  }
})

// ── ACTIVATION IPC ──
// Hub calls check-activation on launch to decide whether to show the
// activation screen or jump straight to the splash. activate-key is the
// form-submit target; returns { ok, info | error }. Rejects bad keys
// loudly but doesn't persist them so the user can retry without leaving
// a broken file behind.
ipcMain.handle('check-activation', async () => {
  try {
    return { ok: true, ...currentActivationState() }
  } catch (e) {
    return { ok: false, error: e.message, activated: false }
  }
})

ipcMain.handle('activate-key', async (event, payload) => {
  try {
    if (!PUB_KEY_PEM) {
      return { ok: false, error: 'app is missing licensing/public_key.pem — contact support.' }
    }
    const key = (payload && typeof payload.key === 'string') ? payload.key.trim() : ''
    if (!key) return { ok: false, error: 'Paste your activation key to continue.' }
    if (!key.startsWith(LICENSE_PREFIX)) {
      return { ok: false,
               error: 'That doesn\'t look like a WillettBot key — keys start with "' + LICENSE_PREFIX + '".' }
    }
    const res = verifyLicenseKey(key, PUB_KEY_PEM)
    if (!res.ok) {
      // Friendly-up the common cases.
      let msg = 'Key rejected: ' + (res.reason || 'unknown')
      if (res.expired) msg = 'This key expired on ' + (res.payload && res.payload.expires) + '.'
      else if (res.reason === 'signature does not match') msg = 'This key isn\'t valid for this build of WillettBot.'
      return { ok: false, error: msg, expired: !!res.expired, info: res.payload || null }
    }
    // Persist. Store the raw key + activatedAt so we can display when it
    // was first entered and re-verify against future public-key rotations.
    const record = {
      key,
      activatedAt: new Date().toISOString(),
      email: res.payload.email,
      tier:  res.payload.tier,
      id:    res.payload.id,
    }
    saveActivation(record)
    return { ok: true, info: res.payload, activatedAt: record.activatedAt, key }
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

// Explicit de-activation. Useful for "switch accounts" during beta and
// keeps us out of weird states where a key gets invalidated upstream.
ipcMain.handle('deactivate', async () => {
  try {
    const p = activationFilePath()
    if (fs.existsSync(p)) fs.unlinkSync(p)
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

// Return just the current set of favorited filenames — used by the hub to
// render the favorites strip without re-reading every script file.
ipcMain.handle('get-favorites', async () => {
  try {
    return { ok: true, favorites: pruneFavorites(loadFavorites()) }
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

// Flip favorite state for one script. If `favorite` is omitted, toggle.
// Returns the new boolean so the UI can update optimistically without
// another round-trip.
ipcMain.handle('toggle-favorite', async (event, payload) => {
  try {
    if (!payload || !payload.filename) {
      return { ok: false, error: 'missing filename' }
    }
    const filename = String(payload.filename).trim()
    const scriptPath = path.join(SCRIPTS_DIR, filename)
    if (!fs.existsSync(scriptPath)) {
      return { ok: false, error: 'Script not found.' }
    }
    const current = new Set(loadFavorites())
    const alreadyFav = current.has(filename)
    let newState
    if (typeof payload.favorite === 'boolean') {
      newState = payload.favorite
    } else {
      newState = !alreadyFav
    }
    if (newState) current.add(filename)
    else current.delete(filename)
    saveFavorites(Array.from(current))
    return { ok: true, favorite: newState }
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

ipcMain.handle('read-script', async (event, filename) => {
  try {
    const full = path.join(SCRIPTS_DIR, filename)
    return { ok: true, content: fs.readFileSync(full, 'utf8') }
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

// Shared script-spawning core. `reply(ch, msg)` is how we forward events
// back to the caller — when triggered from the Run button it's `event.reply`;
// when triggered by the scheduler we pass a shim that broadcasts to mainWin.
function startScriptProc(filename, variables, reply, pythonBin) {
  if (scriptProc) {
    try { scriptProc.kill('SIGTERM') } catch (e) {}
    scriptProc = null
  }
  if (!filename) {
    reply('script-event', { event: 'error', message: 'No script filename provided.' })
    return false
  }
  const scriptPath = path.join(SCRIPTS_DIR, filename)
  if (!fs.existsSync(scriptPath)) {
    reply('script-event', { event: 'error', message: 'Script not found: ' + filename })
    return false
  }

  // If overrides were passed, write a temp script with merged variables.
  let effectivePath = scriptPath
  if (variables && typeof variables === 'object') {
    try {
      const original = JSON.parse(fs.readFileSync(scriptPath, 'utf8'))
      original.variables = Object.assign({}, original.variables || {}, variables)
      const tmp = path.join(os.tmpdir(), 'willettbot_script_override.json')
      fs.writeFileSync(tmp, JSON.stringify(original), 'utf8')
      effectivePath = tmp
    } catch (e) {
      reply('script-event', { event: 'error', message: 'Could not merge variables: ' + e.message })
      return false
    }
  }

  scriptProc = spawn(pythonBin || 'python3', [path.join(PY_DIR, 'runner.py'), effectivePath], {
    stdio: ['pipe', 'pipe', 'pipe']
  })
  installStopHotkey()

  let stdoutBuf = ''
  let stderrBuf = ''
  let sawTerminal = false

  scriptProc.stdout.on('data', (chunk) => {
    stdoutBuf += chunk.toString()
    let nl
    while ((nl = stdoutBuf.indexOf('\n')) !== -1) {
      const line = stdoutBuf.slice(0, nl).trim()
      stdoutBuf = stdoutBuf.slice(nl + 1)
      if (!line) continue
      let evt
      try { evt = JSON.parse(line) } catch (_) { continue }

      // Special handling for prompts: confirms become top-right toast
      // notifications (macOS-style) that float over whatever app you're in;
      // inputs still use the in-app modal, but we force the window to the
      // front so the user sees it.
      if (evt.event === 'prompt') {
        const kind = evt.kind || 'confirm'
        if (kind === 'confirm') {
          showToast({
            message: evt.message || 'Continue?',
            confirmLabel: evt.confirmLabel || null,
            cancelLabel: evt.cancelLabel || null
          }).then((accepted) => {
            const cancelled = !accepted
            try {
              if (scriptProc && scriptProc.stdin && !scriptProc.stdin.destroyed) {
                scriptProc.stdin.write(JSON.stringify({
                  id: evt.id,
                  cancelled: cancelled
                }) + '\n')
              }
            } catch (e) { console.error('[prompt confirm] write failed:', e) }
            reply('script-event', {
              event: 'log',
              message: cancelled ? '✖ You cancelled at the confirmation toast.' : '✓ Confirmed.'
            })
          })
          continue
        } else {
          try {
            if (mainWin) {
              if (mainWin.isMinimized()) mainWin.restore()
              mainWin.show()
              mainWin.focus()
              if (app.focus) app.focus({ steal: true })
            }
          } catch (e) { /* non-fatal */ }
          reply('script-event', evt)
          continue
        }
      }

      reply('script-event', evt)
      if (evt.event === 'script-done' || evt.event === 'error' || evt.event === 'failsafe' || evt.event === 'stopped') {
        sawTerminal = true
      }
    }
  })

  scriptProc.stderr.on('data', (chunk) => {
    stderrBuf += chunk.toString()
    console.error('[runner]', chunk.toString())
  })

  scriptProc.on('close', (code) => {
    scriptProc = null
    if (!clickerProc && !recorderProc) uninstallStopHotkey()
    if (!sawTerminal) {
      const tail = stderrBuf.trim().split('\n').pop() || ('exited with code ' + code)
      reply('script-event', { event: 'error', message: tail })
    }
  })

  scriptProc.on('error', (err) => {
    reply('script-event', { event: 'error', message: 'Failed to start runner: ' + err.message })
    scriptProc = null
  })

  return true
}

// Activation gate for anything that actually automates the desktop.
// The hub is expected to block these UIs behind the activation screen, but
// we enforce server-side too so a poked renderer can't sneak past.
function assertActivatedOrReply(replyFn) {
  const st = currentActivationState()
  if (st.activated) return true
  replyFn('script-event', {
    event: 'error',
    message: 'WillettBot isn\'t activated. Enter your activation key to run scripts.'
  })
  return false
}

// ── SCRIPTS: start a script runner (triggered by the Run button) ──
ipcMain.on('run-script', async (event, payload) => {
  const reply = (ch, msg) => event.reply(ch, msg)
  if (!assertActivatedOrReply(reply)) return
  let py
  try { py = await ensurePythonEnv() }
  catch (e) {
    reply('script-event', { event: 'error', message: 'Python setup failed: ' + e.message })
    return
  }
  const filename = payload && payload.filename
  const variables = payload && payload.variables
  startScriptProc(filename, variables, reply, py)
})

// ── SCRIPTS: forward a prompt response from the UI to the runner's stdin ──
ipcMain.on('script-prompt-response', (event, response) => {
  if (!scriptProc || !scriptProc.stdin || scriptProc.stdin.destroyed) return
  try {
    scriptProc.stdin.write(JSON.stringify(response) + '\n')
  } catch (e) {
    console.error('[script-prompt-response] write failed:', e)
  }
})

// ── SCRIPTS: stop the runner ──
ipcMain.on('stop-script', () => {
  if (scriptProc) {
    try { scriptProc.kill('SIGTERM') } catch (e) {}
    scriptProc = null
  }
})

// ── SCRIPTS: overwrite a script's JSON from the editor ──
// Used by the Edit modal in hub.html. Rejects invalid JSON or bad filenames
// so a typo can't clobber a script with garbage.
ipcMain.handle('write-script', async (event, payload) => {
  try {
    if (!payload || !payload.filename || typeof payload.content !== 'string') {
      return { ok: false, error: 'missing fields' }
    }
    let filename = String(payload.filename).trim()
    if (!filename.endsWith('.json')) filename += '.json'
    if (!/^[A-Za-z0-9_\-. ]+$/.test(filename)) {
      return { ok: false, error: 'Filename may only contain letters, numbers, spaces, _, -, or .' }
    }
    // Parse-check the content so we don't write a broken script.
    let parsed
    try { parsed = JSON.parse(payload.content) }
    catch (e) { return { ok: false, error: 'Invalid JSON: ' + e.message } }
    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.actions)) {
      return { ok: false, error: 'Script must be an object with an "actions" array.' }
    }
    const full = path.join(SCRIPTS_DIR, filename)
    fs.writeFileSync(full, JSON.stringify(parsed, null, 2), 'utf8')
    return { ok: true, filename: filename }
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

// ── SCRIPTS: delete a script ──
ipcMain.handle('delete-script', async (event, filename) => {
  try {
    if (!filename) return { ok: false, error: 'missing filename' }
    const safe = String(filename).trim()
    if (!/^[A-Za-z0-9_\-. ]+$/.test(safe)) {
      return { ok: false, error: 'Unsafe filename.' }
    }
    const full = path.join(SCRIPTS_DIR, safe)
    if (!fs.existsSync(full)) return { ok: false, error: 'Not found.' }
    fs.unlinkSync(full)
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

// ── RECORDER: start a mirroring session ──
// Spawns recorder.py with the chosen start/end hotkeys. The recorder emits
// JSON event lines on stdout; we forward each one to the renderer. When it
// emits `done` with a compiled script, the renderer shows a Save panel — the
// actual save goes through `save-recorded-script` below so the user picks
// the filename/description.
ipcMain.on('record-start', async (event, opts) => {
  // Activation gate: recording is an automation feature too.
  if (!currentActivationState().activated) {
    event.reply('record-event', {
      event: 'error',
      message: 'WillettBot isn\'t activated — enter your key to enable recording.'
    })
    return
  }
  let py
  try { py = await ensurePythonEnv() }
  catch (e) {
    event.reply('record-event', { event: 'error', message: 'Python setup failed: ' + e.message })
    return
  }
  opts = opts || {}
  if (recorderProc) {
    try { recorderProc.kill('SIGTERM') } catch (e) {}
    recorderProc = null
  }
  const args = [
    path.join(PY_DIR, 'recorder.py'),
    '--start-hotkey', opts.startHotkey || 'f9',
    '--end-hotkey',   opts.endHotkey   || 'f10',
    '--name',         opts.name        || 'Recorded Script',
    '--description',  opts.description || 'Recorded via the mirroring feature.'
  ]
  // Auto-start bypass: UI can request "start recording NOW" instead of
  // waiting for the start hotkey. Needed when the bundled Python doesn't
  // have Input Monitoring granted (the hotkey listener sees nothing).
  if (opts.autoStart) args.push('--auto-start')
  recorderProc = spawn(py, args, { stdio: ['pipe', 'pipe', 'pipe'] })

  let stdoutBuf = ''
  let stderrBuf = ''
  let sawDone = false

  recorderProc.stdout.on('data', (chunk) => {
    stdoutBuf += chunk.toString()
    let nl
    while ((nl = stdoutBuf.indexOf('\n')) !== -1) {
      const line = stdoutBuf.slice(0, nl).trim()
      stdoutBuf = stdoutBuf.slice(nl + 1)
      if (!line) continue
      let evt
      try { evt = JSON.parse(line) } catch (_) { continue }
      if (evt.event === 'done' || evt.event === 'error') sawDone = true
      event.reply('record-event', evt)
    }
  })

  recorderProc.stderr.on('data', (chunk) => {
    stderrBuf += chunk.toString()
    console.error('[recorder]', chunk.toString())
  })

  recorderProc.on('close', (code) => {
    recorderProc = null
    if (!sawDone) {
      const tail = stderrBuf.trim().split('\n').pop() || ('exited ' + code)
      event.reply('record-event', { event: 'exit', code: code, trailing: tail })
    }
  })

  recorderProc.on('error', (err) => {
    event.reply('record-event', {
      event: 'error',
      message: 'Failed to start recorder: ' + err.message
    })
    recorderProc = null
  })
})

// ── RECORDER: cancel (user bailed out without pressing the end hotkey) ──
ipcMain.on('record-stop', () => {
  if (recorderProc) {
    try { recorderProc.kill('SIGTERM') } catch (e) {}
    recorderProc = null
  }
})

// ── RECORDER: save the compiled script under a user-chosen filename ──
ipcMain.handle('save-recorded-script', async (event, payload) => {
  try {
    if (!payload || !payload.script || !payload.filename) {
      return { ok: false, error: 'Missing filename or script.' }
    }
    let filename = String(payload.filename).trim()
    if (!filename.endsWith('.json')) filename += '.json'
    if (!/^[A-Za-z0-9_\-. ]+$/.test(filename)) {
      return { ok: false, error: 'Filename may only contain letters, numbers, spaces, _, -, or .' }
    }
    // Apply any user-supplied display name / description overrides.
    const script = Object.assign({}, payload.script)
    if (payload.displayName) script.name = String(payload.displayName)
    if (payload.description) script.description = String(payload.description)
    if (!Array.isArray(script.actions)) script.actions = []
    if (!script.variables || typeof script.variables !== 'object') script.variables = {}

    const full = path.join(SCRIPTS_DIR, filename)
    if (fs.existsSync(full) && !payload.overwrite) {
      return {
        ok: false,
        conflict: true,
        filename: filename,
        error: 'A file named "' + filename + '" already exists.'
      }
    }
    fs.writeFileSync(full, JSON.stringify(script, null, 2), 'utf8')
    return { ok: true, filename: filename }
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

// ── SCHEDULER ─────────────────────────────────────────────────────────────
// Persisted schedules (JSON on disk). Each item:
// {
//   id:          "uuid",
//   filename:    "send_gmail.json",
//   scriptName:  "Send Gmail",               // cached for display
//   variables:   { to:..., subject:..., body:... },
//   mode:        "once" | "recurring",
//   onceDate:    "2026-04-25",               // if mode=once
//   onceTime:    "14:00",                    // if mode=once
//   recurringDay:  "daily" | "monday" ...,    // if mode=recurring
//   recurringTime: "09:30",                   // if mode=recurring
//   nextRun:     1712345678000,              // ms epoch — recomputed on save + after fire
//   enabled:     true,
//   lastRun:     1712345678000 | null
// }

const SCHEDULES_FILE = path.join(DATA_DIR, 'schedules.json')
const DAY_OF_WEEK = { sunday:0, monday:1, tuesday:2, wednesday:3, thursday:4, friday:5, saturday:6 }

function loadSchedules() {
  try {
    if (!fs.existsSync(SCHEDULES_FILE)) return { version: 1, items: [] }
    const raw = fs.readFileSync(SCHEDULES_FILE, 'utf8')
    const parsed = JSON.parse(raw)
    if (!parsed || !Array.isArray(parsed.items)) return { version: 1, items: [] }
    return parsed
  } catch (e) {
    console.error('[scheduler] failed to load:', e)
    return { version: 1, items: [] }
  }
}

function saveSchedulesToDisk(data) {
  try {
    fs.writeFileSync(SCHEDULES_FILE, JSON.stringify(data, null, 2), 'utf8')
  } catch (e) {
    console.error('[scheduler] failed to save:', e)
  }
}

// Returns ms epoch timestamp for the next scheduled run, or null if none.
// Pass `after` to compute the next run strictly after that moment (used when
// a recurring schedule fires — we advance past the one that just ran).
function computeNextRun(sch, after) {
  const now = after instanceof Date ? after : new Date(after || Date.now())
  if (sch.mode === 'once') {
    if (!sch.onceDate || !sch.onceTime) return null
    const [y, mo, d] = sch.onceDate.split('-').map(Number)
    const [h, m] = sch.onceTime.split(':').map(Number)
    const dt = new Date(y, (mo || 1) - 1, d || 1, h || 0, m || 0, 0, 0)
    const t = dt.getTime()
    return t > now.getTime() ? t : null   // one-time in the past = done
  }
  if (sch.mode === 'recurring') {
    if (!sch.recurringTime) return null
    const [h, m] = sch.recurringTime.split(':').map(Number)
    const candidate = new Date(now)
    candidate.setSeconds(0, 0)
    candidate.setHours(h || 0, m || 0, 0, 0)
    if (sch.recurringDay === 'daily' || !sch.recurringDay) {
      if (candidate.getTime() <= now.getTime()) candidate.setDate(candidate.getDate() + 1)
      return candidate.getTime()
    }
    const targetDow = DAY_OF_WEEK[sch.recurringDay]
    if (targetDow === undefined) return null
    let deltaDays = (targetDow - candidate.getDay() + 7) % 7
    if (deltaDays === 0 && candidate.getTime() <= now.getTime()) deltaDays = 7
    candidate.setDate(candidate.getDate() + deltaDays)
    return candidate.getTime()
  }
  return null
}

async function fireSchedule(sch) {
  // Broadcast a log event so the user sees it in the builder view.
  const reply = (ch, msg) => {
    if (mainWin && !mainWin.isDestroyed() && mainWin.webContents) {
      try { mainWin.webContents.send(ch, msg) } catch (e) {}
    }
  }
  reply('script-event', {
    event: 'log',
    message: '⏰ Schedule firing: ' + (sch.scriptName || sch.filename)
  })
  let py
  try { py = await ensurePythonEnv() }
  catch (e) {
    reply('script-event', { event: 'error', message: 'Python setup failed: ' + e.message })
    return false
  }
  return startScriptProc(sch.filename, sch.variables || {}, reply, py)
}

// Scheduler tick: every 30s, scan schedules and fire any that are due.
let schedulerInterval = null

function schedulerTick() {
  const data = loadSchedules()
  const now = Date.now()
  let changed = false
  for (const sch of data.items) {
    if (!sch.enabled) continue
    if (!sch.nextRun) {
      sch.nextRun = computeNextRun(sch)
      changed = true
      if (!sch.nextRun) { sch.enabled = false; continue }
    }
    if (now >= sch.nextRun) {
      fireSchedule(sch)
      sch.lastRun = now
      if (sch.mode === 'once') {
        sch.enabled = false
        sch.nextRun = null
      } else {
        // Recurring: advance nextRun to the next occurrence strictly after now.
        sch.nextRun = computeNextRun(sch, new Date(now + 1000))
      }
      changed = true
      // Tell the renderer to refresh the schedules list.
      if (mainWin && !mainWin.isDestroyed() && mainWin.webContents) {
        try { mainWin.webContents.send('schedules-changed', {}) } catch (e) {}
      }
    }
  }
  if (changed) saveSchedulesToDisk(data)
}

function startScheduler() {
  if (schedulerInterval) return
  // First tick a few seconds after boot so the UI has time to mount, then 30s.
  setTimeout(schedulerTick, 3000)
  schedulerInterval = setInterval(schedulerTick, 30 * 1000)
}

app.whenReady().then(startScheduler)

app.on('before-quit', () => {
  if (schedulerInterval) { clearInterval(schedulerInterval); schedulerInterval = null }
})

// ── SCHEDULES: IPC ──────────────────────────────────────────────────────────

ipcMain.handle('list-schedules', async () => {
  const data = loadSchedules()
  // Re-fill any missing nextRun so the UI can show it immediately.
  let mutated = false
  for (const sch of data.items) {
    if (sch.enabled && !sch.nextRun) {
      sch.nextRun = computeNextRun(sch)
      if (!sch.nextRun) sch.enabled = false
      mutated = true
    }
  }
  if (mutated) saveSchedulesToDisk(data)
  return data.items
})

ipcMain.handle('save-schedule', async (event, item) => {
  if (!item || !item.filename || !item.mode) return { ok: false, error: 'missing fields' }
  const data = loadSchedules()
  const id = item.id || String(Date.now()) + '-' + Math.floor(Math.random() * 1e6)
  const enriched = Object.assign({}, item, {
    id: id,
    enabled: item.enabled !== false,
    lastRun: item.lastRun || null
  })
  enriched.nextRun = computeNextRun(enriched)
  if (!enriched.nextRun) return { ok: false, error: 'That date/time is in the past.' }
  // Replace if id matches, else append.
  const idx = data.items.findIndex(x => x.id === id)
  if (idx >= 0) data.items[idx] = enriched
  else data.items.push(enriched)
  saveSchedulesToDisk(data)
  if (mainWin && mainWin.webContents) {
    try { mainWin.webContents.send('schedules-changed', {}) } catch (e) {}
  }
  return { ok: true, schedule: enriched }
})

ipcMain.handle('delete-schedule', async (event, id) => {
  const data = loadSchedules()
  const before = data.items.length
  data.items = data.items.filter(x => x.id !== id)
  saveSchedulesToDisk(data)
  if (mainWin && mainWin.webContents) {
    try { mainWin.webContents.send('schedules-changed', {}) } catch (e) {}
  }
  return { ok: true, removed: before - data.items.length }
})
