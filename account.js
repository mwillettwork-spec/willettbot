// ──────────────────────────────────────────────────────────────────────────────
// account.js — email/account-based licensing for the desktop app.
//
// Replaces the old offline Ed25519-signed license-key flow (licensing/keygen.js)
// with a server-checked subscription model. Both flows are kept in main.js for
// a transition period; this module only handles the new path.
//
// Flow on first launch with no token stored:
//
//   1. Renderer asks main to start sign-in.
//   2. We pick a random loopback port, spin up a tiny HTTP server, generate a
//      CSRF `state` nonce, and open the user's default browser to
//        https://www.willettbot.com/desktop-auth?state=…&port=…&device=…
//   3. They complete (or already have) a Clerk magic-link sign-in. The
//      /desktop-auth page mints a device token and redirects the browser to
//        http://127.0.0.1:<port>/callback?state=…&token=wb_…
//   4. Our local server validates `state`, captures the token, persists it
//      to <userData>/account.json, and shuts itself down.
//   5. From now on, the desktop app sends `Authorization: Bearer wb_…` to
//      /api/license to check whether the user is licensed.
//
// All network calls are best-effort: if the server is unreachable we fall
// back to a cached license verdict so users on flaky internet aren't locked
// out of an app they've already paid for.
// ──────────────────────────────────────────────────────────────────────────────

const http = require('http')
const crypto = require('crypto')
const path = require('path')
const fs = require('fs')
const os = require('os')
const { URL } = require('url')
const { app, shell } = require('electron')

// Override via env for local dev (e.g. WILLETTBOT_WEB_BASE=http://localhost:3000).
const WEB_BASE = process.env.WILLETTBOT_WEB_BASE || 'https://www.willettbot.com'

// How long we'll wait for the user to finish signing in before giving up
// on this attempt. Generous because magic-link emails can take a minute.
const SIGNIN_TIMEOUT_MS = 5 * 60 * 1000

// How long a successful /api/license response is treated as good without a
// re-check. Short enough that cancellations propagate within a day; long
// enough that brief network blips don't lock anyone out.
const LICENSE_CACHE_TTL_MS = 24 * 60 * 60 * 1000

// Hard offline-grace cap: if we can't reach the server but the last
// successful license check was within this window, we still treat the user
// as activated. Past this they'll need to come back online.
const LICENSE_OFFLINE_GRACE_MS = 7 * 24 * 60 * 60 * 1000

// Network timeout for /api/license calls. We don't want a slow server call
// to wedge the app on launch.
const LICENSE_FETCH_TIMEOUT_MS = 10 * 1000

// Subscription statuses that count as "licensed". Mirrors the server-side
// filter in /api/license — keep these in sync if the server list changes.
const LIVE_STATUSES = new Set(['active', 'trialing', 'past_due'])


// ── Storage ───────────────────────────────────────────────────────────────────

// Stored at <userData>/account.json so it survives app restarts and updates.
// Schema:
//   {
//     token:           "wb_…",          // device token from /api/desktop-auth/exchange
//     signedInAt:      ISO timestamp,    // when the token was minted
//     email:           string|null,      // last email we saw on /api/license
//     lastLicense: {                    // last /api/license response, for offline grace
//       status: "active"|"inactive",
//       tier, plan, subscription_status,
//       current_period_end, cancel_at_period_end,
//       checkedAt: ISO timestamp
//     } | null
//   }
let _accountFilePath = null
function accountFilePath() {
  if (_accountFilePath) return _accountFilePath
  try {
    _accountFilePath = path.join(app.getPath('userData'), 'account.json')
  } catch (e) {
    // Off-Electron test harness fallback.
    _accountFilePath = path.join(os.tmpdir(), 'willettbot-account.json')
  }
  return _accountFilePath
}

function loadAccount() {
  try {
    const p = accountFilePath()
    if (!fs.existsSync(p)) return null
    const raw = JSON.parse(fs.readFileSync(p, 'utf8'))
    if (!raw || typeof raw !== 'object' || typeof raw.token !== 'string') return null
    return raw
  } catch (e) {
    console.error('[account] load failed:', e)
    return null
  }
}

function saveAccount(record) {
  try {
    const p = accountFilePath()
    fs.mkdirSync(path.dirname(p), { recursive: true })
    fs.writeFileSync(p, JSON.stringify(record, null, 2), 'utf8')
    return true
  } catch (e) {
    console.error('[account] save failed:', e)
    return false
  }
}

function clearAccount() {
  try {
    const p = accountFilePath()
    if (fs.existsSync(p)) fs.unlinkSync(p)
    return true
  } catch (e) {
    console.error('[account] clear failed:', e)
    return false
  }
}


// ── Browser-handoff sign-in ──────────────────────────────────────────────────

/**
 * Run the browser-handoff sign-in flow. Resolves with the new account record
 * on success, rejects with an Error on failure or timeout.
 *
 * Caller (main.js) is responsible for surfacing progress / errors to the UI;
 * this function just does the dance and returns once it's done.
 */
async function signIn({ deviceName } = {}) {
  // 32 hex chars = 128 bits of entropy, which is plenty for CSRF.
  const state = crypto.randomBytes(16).toString('hex')
  const device = (deviceName || os.hostname() || 'WillettBot Desktop').slice(0, 80)

  return new Promise((resolve, reject) => {
    let resolved = false
    let timeoutHandle = null

    // Tiny HTTP server bound to loopback. Listens for the one redirect, then
    // closes itself. We bind 127.0.0.1 explicitly so we never expose this on
    // a LAN interface, even on funky network configurations.
    const server = http.createServer((req, res) => {
      try {
        const url = new URL(req.url, `http://127.0.0.1`)
        if (url.pathname !== '/callback') {
          res.writeHead(404, { 'Content-Type': 'text/plain' })
          res.end('Not found')
          return
        }
        const cbState = url.searchParams.get('state')
        const cbToken = url.searchParams.get('token')

        // CSRF check: the state must match the one we generated. Without
        // this, a malicious page could redirect any signed-in user's
        // browser to our callback and pre-fill the desktop with their
        // attacker-controlled token.
        if (cbState !== state) {
          res.writeHead(400, { 'Content-Type': 'text/plain' })
          res.end('State mismatch — please try signing in again from WillettBot.')
          return
        }
        if (!cbToken || !cbToken.startsWith('wb_')) {
          res.writeHead(400, { 'Content-Type': 'text/plain' })
          res.end('Missing token — please try signing in again from WillettBot.')
          return
        }

        // Friendly success page so the user knows they can close the tab
        // and switch back to the desktop app.
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
        res.end(`<!doctype html><html><head><meta charset="utf-8"><title>Signed in</title>
<style>body{font:14px -apple-system,system-ui,sans-serif;color:#111;
background:#fafafa;display:flex;align-items:center;justify-content:center;
height:100vh;margin:0}.card{background:#fff;border:1px solid #e5e5e5;
border-radius:10px;padding:32px 40px;text-align:center;max-width:360px}
h1{margin:0 0 8px;font-size:18px}p{color:#555;margin:0}</style></head>
<body><div class="card"><h1>You're signed in.</h1>
<p>You can close this tab and head back to WillettBot.</p></div></body></html>`)

        finish(null, cbToken)
      } catch (err) {
        finish(err)
      }
    })

    function finish(err, token) {
      if (resolved) return
      resolved = true
      if (timeoutHandle) clearTimeout(timeoutHandle)
      // Close the server but don't block the caller waiting for stragglers.
      try { server.close() } catch (_) {}
      if (err) return reject(err)

      const record = {
        token,
        signedInAt: new Date().toISOString(),
        email: null,
        lastLicense: null,
      }
      saveAccount(record)
      resolve(record)
    }

    // Pick a random free loopback port. listen(0) lets the OS pick one,
    // then we read it off the server. Limiting to 127.0.0.1 keeps the
    // callback URL valid only on this machine.
    server.on('error', (err) => finish(err))
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      const u = new URL('/desktop-auth', WEB_BASE)
      u.searchParams.set('state', state)
      u.searchParams.set('port', String(port))
      u.searchParams.set('device', device)

      // Open in the user's default browser. shell.openExternal returns a
      // Promise that resolves once the OS hands off — not when the user
      // finishes signing in — so we don't await its completion meaningfully.
      shell.openExternal(u.toString()).catch((openErr) => {
        finish(new Error('Could not open browser: ' + openErr.message))
      })

      timeoutHandle = setTimeout(() => {
        finish(new Error('Sign-in timed out. Please try again.'))
      }, SIGNIN_TIMEOUT_MS)
    })
  })
}


// ── License lookup ──────────────────────────────────────────────────────────

/**
 * Fetch fresh license state from /api/license. Returns the parsed payload on
 * 200, throws on non-2xx or transport error. Callers should catch and decide
 * whether to fall back to the cached verdict.
 */
async function fetchLicenseFromServer(token) {
  const u = new URL('/api/license', WEB_BASE)
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), LICENSE_FETCH_TIMEOUT_MS)
  try {
    const res = await fetch(u.toString(), {
      method: 'GET',
      headers: { Authorization: 'Bearer ' + token },
      signal: ctrl.signal,
    })
    if (res.status === 401) {
      const err = new Error('unauthorized')
      err.status = 401
      throw err
    }
    if (!res.ok) {
      throw new Error('license check failed: HTTP ' + res.status)
    }
    return await res.json()
  } finally {
    clearTimeout(t)
  }
}

/**
 * High-level license check. Tries the server first; on any network failure
 * falls back to the cached `lastLicense` from disk if it's within the
 * offline-grace window.
 *
 * Resolves to:
 *   {
 *     activated: boolean,
 *     reason?: 'no-account' | 'unauthorized' | 'inactive' | 'offline-stale' | 'error',
 *     payload?: { ...license fields... },   // when activated, or last-known state
 *     fromCache?: boolean,                   // true when we fell back to cached
 *     error?: string,
 *   }
 */
async function getLicenseState({ forceRefresh = false } = {}) {
  const account = loadAccount()
  if (!account || !account.token) {
    return { activated: false, reason: 'no-account' }
  }

  // Hot path: we already polled recently and the result was active, and the
  // caller didn't ask for a forced refresh. Avoids spamming the server on
  // every recorder/runner gate check.
  if (!forceRefresh && account.lastLicense) {
    const checkedAt = Date.parse(account.lastLicense.checkedAt || '')
    const fresh = Number.isFinite(checkedAt) &&
                  (Date.now() - checkedAt) < LICENSE_CACHE_TTL_MS
    if (fresh && account.lastLicense.status === 'active') {
      return {
        activated: true,
        payload: account.lastLicense,
        fromCache: true,
      }
    }
  }

  // Cold path / forced refresh: hit the server.
  try {
    const payload = await fetchLicenseFromServer(account.token)
    const checkedAt = new Date().toISOString()
    const cached = {
      status: payload.status,
      tier: payload.tier,
      plan: payload.plan,
      subscription_status: payload.subscription_status,
      current_period_end: payload.current_period_end,
      cancel_at_period_end: !!payload.cancel_at_period_end,
      checkedAt,
    }
    saveAccount({
      ...account,
      email: payload.email ?? account.email ?? null,
      lastLicense: cached,
    })
    if (payload.status === 'active') {
      return { activated: true, payload: cached }
    }
    return { activated: false, reason: 'inactive', payload: cached }
  } catch (err) {
    // Token rejected by the server — clear it; user has to sign in again.
    if (err && err.status === 401) {
      clearAccount()
      return { activated: false, reason: 'unauthorized', error: err.message }
    }

    // Network error: fall back to cached verdict if it's recent enough.
    const cached = account.lastLicense
    if (cached && cached.status === 'active') {
      const checkedAt = Date.parse(cached.checkedAt || '')
      const within = Number.isFinite(checkedAt) &&
                     (Date.now() - checkedAt) < LICENSE_OFFLINE_GRACE_MS
      if (within) {
        return { activated: true, payload: cached, fromCache: true }
      }
      return { activated: false, reason: 'offline-stale', payload: cached, error: err.message }
    }
    return { activated: false, reason: 'error', error: err.message }
  }
}


/**
 * Synchronous cache-only license check. Reads account.json off disk and
 * decides whether the user is currently licensed *without ever touching
 * the network*. Used by the gate that fires before every recorder/runner
 * action, where we can't afford a server round-trip.
 *
 * Returns { activated, reason?, payload?, fromCache: true }.
 *
 *   - activated:true     → token is present, last server check said active,
 *                          and that check is within the offline-grace window
 *   - activated:false    → no token, never checked, last status was not
 *                          active, or cache is older than offline-grace
 *
 * This is deliberately conservative: if the cache is stale we report
 * inactive even if the last status was active. The async getLicenseState()
 * is what refreshes the cache; we run it in the background on app launch.
 */
function getCachedLicenseState() {
  const account = loadAccount()
  if (!account || !account.token) {
    return { activated: false, reason: 'no-account', fromCache: true }
  }
  const cached = account.lastLicense
  if (!cached) {
    return { activated: false, reason: 'never-checked', fromCache: true }
  }
  const checkedAt = Date.parse(cached.checkedAt || '')
  if (!Number.isFinite(checkedAt) ||
      (Date.now() - checkedAt) >= LICENSE_OFFLINE_GRACE_MS) {
    return { activated: false, reason: 'cache-expired', payload: cached, fromCache: true }
  }
  if (cached.status === 'active') {
    return { activated: true, payload: cached, fromCache: true }
  }
  return { activated: false, reason: 'inactive', payload: cached, fromCache: true }
}


/** Best-effort sign-out — drops the local token and cached license. */
function signOut() {
  return clearAccount()
}


/** Read-only view of who's signed in, for the renderer to render. */
function getCurrentAccountSummary() {
  const account = loadAccount()
  if (!account) return null
  return {
    email: account.email || null,
    signedInAt: account.signedInAt || null,
    lastStatus: account.lastLicense?.status || null,
    lastCheckedAt: account.lastLicense?.checkedAt || null,
  }
}


module.exports = {
  signIn,
  signOut,
  getLicenseState,
  getCachedLicenseState,
  getCurrentAccountSummary,
  loadAccount,
  // Exposed for test/debug use; main.js shouldn't normally need these.
  clearAccount,
  WEB_BASE,
  LIVE_STATUSES,
}
