#!/usr/bin/env node
// WillettBot license tooling — Ed25519-signed activation keys.
//
// Subcommands:
//   init
//     Generates a keypair in this folder:
//       public_key.pem   — bundled with the app (safe to ship)
//       private_key.pem  — stays on your machine / server; git-ignore it!
//     Refuses to overwrite if either file already exists unless --force.
//
//   mint --email <addr> [--days <n>] [--tier <name>] [--note <text>]
//     Prints a single activation key of the form:
//       willettbot-<base64url(payload)>.<base64url(sig)>
//     Payload is JSON: { email, issued (ISO), expires (ISO), tier, note?, id }.
//     Default --days 90, default --tier beta.
//     Verify round-trip is sanity-checked before printing.
//
//   verify <key>
//     Local sanity-check: prints payload + "ok" or the validation error.
//
// Usage (from /.../willettbot/licensing):
//   node keygen.js init
//   node keygen.js mint --email me@example.com --days 120 --tier beta
//   node keygen.js verify willettbot-...
//
// No external deps — uses Node's built-in `crypto`.

const fs = require('fs')
const path = require('path')
const crypto = require('crypto')

const HERE = __dirname
const PUB_PATH  = path.join(HERE, 'public_key.pem')
const PRIV_PATH = path.join(HERE, 'private_key.pem')

const PREFIX = 'willettbot-'

// ── arg parsing ────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const sub = argv[2]
  const rest = argv.slice(3)
  const flags = {}
  const positional = []
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i]
    if (a.startsWith('--')) {
      const key = a.slice(2)
      const next = rest[i + 1]
      if (next === undefined || next.startsWith('--')) {
        flags[key] = true
      } else {
        flags[key] = next
        i++
      }
    } else {
      positional.push(a)
    }
  }
  return { sub, flags, positional }
}

// ── base64url helpers (RFC 4648) ───────────────────────────────────────────
function b64urlEncode(buf) {
  return Buffer.from(buf).toString('base64')
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
function b64urlDecode(s) {
  s = String(s).replace(/-/g, '+').replace(/_/g, '/')
  while (s.length % 4) s += '='
  return Buffer.from(s, 'base64')
}

// ── subcommand: init ───────────────────────────────────────────────────────
function cmdInit(flags) {
  const force = !!flags.force
  if ((fs.existsSync(PUB_PATH) || fs.existsSync(PRIV_PATH)) && !force) {
    console.error('keys already exist in', HERE)
    console.error('  ', PUB_PATH)
    console.error('  ', PRIV_PATH)
    console.error('pass --force to overwrite (this invalidates every key minted with the old pair).')
    process.exit(2)
  }
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519')
  const pubPem  = publicKey.export({ type: 'spki',  format: 'pem' })
  const privPem = privateKey.export({ type: 'pkcs8', format: 'pem' })
  fs.writeFileSync(PUB_PATH,  pubPem,  { mode: 0o644 })
  fs.writeFileSync(PRIV_PATH, privPem, { mode: 0o600 })
  console.log('wrote', PUB_PATH)
  console.log('wrote', PRIV_PATH, '(keep this secret!)')
  console.log('Add licensing/private_key.pem to .gitignore.')
}

// ── subcommand: mint ───────────────────────────────────────────────────────
function cmdMint(flags) {
  if (!fs.existsSync(PRIV_PATH)) {
    console.error('no private key found at', PRIV_PATH)
    console.error('run `node keygen.js init` first.')
    process.exit(2)
  }
  const email = flags.email
  if (!email || typeof email !== 'string') {
    console.error('--email <addr> is required')
    process.exit(2)
  }
  const days = Number(flags.days || 90)
  if (!Number.isFinite(days) || days <= 0) {
    console.error('--days must be a positive number')
    process.exit(2)
  }
  const tier = (typeof flags.tier === 'string' && flags.tier) || 'beta'
  const note = typeof flags.note === 'string' ? flags.note : undefined

  const issued  = new Date()
  const expires = new Date(issued.getTime() + days * 24 * 60 * 60 * 1000)
  const id = crypto.randomBytes(6).toString('hex')

  const payload = {
    email: email.trim().toLowerCase(),
    tier,
    issued:  issued.toISOString(),
    expires: expires.toISOString(),
    id,
  }
  if (note) payload.note = note

  const privPem = fs.readFileSync(PRIV_PATH, 'utf8')
  const priv = crypto.createPrivateKey(privPem)

  const payloadBuf = Buffer.from(JSON.stringify(payload), 'utf8')
  const sig = crypto.sign(null, payloadBuf, priv)   // Ed25519: algo = null

  const key = PREFIX + b64urlEncode(payloadBuf) + '.' + b64urlEncode(sig)

  // Round-trip self-check so we never ship a bad key.
  const pubPem = fs.readFileSync(PUB_PATH, 'utf8')
  const pub = crypto.createPublicKey(pubPem)
  const ok = crypto.verify(null, payloadBuf, pub, sig)
  if (!ok) {
    console.error('internal error: minted key failed its own verification.')
    process.exit(3)
  }

  console.log('--- activation key ---')
  console.log(key)
  console.log('---')
  console.log('email:   ', payload.email)
  console.log('tier:    ', payload.tier)
  console.log('issued:  ', payload.issued)
  console.log('expires: ', payload.expires)
  console.log('id:      ', payload.id)
  if (note) console.log('note:    ', note)
}

// ── subcommand: verify ─────────────────────────────────────────────────────
// Exposed as a library function too so main.js can reuse it.
function verifyKey(keyStr, pubPem) {
  if (typeof keyStr !== 'string' || !keyStr.startsWith(PREFIX)) {
    return { ok: false, reason: 'bad format (missing prefix)' }
  }
  const body = keyStr.slice(PREFIX.length)
  const dot = body.indexOf('.')
  if (dot < 1 || dot === body.length - 1) {
    return { ok: false, reason: 'bad format (missing signature)' }
  }
  const payloadB64 = body.slice(0, dot)
  const sigB64     = body.slice(dot + 1)
  let payloadBuf, sigBuf
  try {
    payloadBuf = b64urlDecode(payloadB64)
    sigBuf     = b64urlDecode(sigB64)
  } catch (e) {
    return { ok: false, reason: 'bad base64' }
  }
  let payload
  try {
    payload = JSON.parse(payloadBuf.toString('utf8'))
  } catch (e) {
    return { ok: false, reason: 'payload is not valid JSON' }
  }
  let pub
  try {
    pub = crypto.createPublicKey(pubPem)
  } catch (e) {
    return { ok: false, reason: 'public key unreadable: ' + e.message }
  }
  let sigOk = false
  try {
    sigOk = crypto.verify(null, payloadBuf, pub, sigBuf)
  } catch (e) {
    return { ok: false, reason: 'verify threw: ' + e.message }
  }
  if (!sigOk) return { ok: false, reason: 'signature does not match' }
  if (!payload.expires) return { ok: false, reason: 'payload missing expires' }
  const exp = Date.parse(payload.expires)
  if (!Number.isFinite(exp)) return { ok: false, reason: 'payload expires unparseable' }
  if (exp < Date.now()) return { ok: false, reason: 'key expired', expired: true, payload }
  return { ok: true, payload }
}

function cmdVerify(positional) {
  const key = positional[0]
  if (!key) { console.error('usage: verify <key>'); process.exit(2) }
  if (!fs.existsSync(PUB_PATH)) {
    console.error('no public key at', PUB_PATH)
    process.exit(2)
  }
  const pubPem = fs.readFileSync(PUB_PATH, 'utf8')
  const res = verifyKey(key, pubPem)
  console.log(JSON.stringify(res, null, 2))
  process.exit(res.ok ? 0 : 1)
}

// ── main ───────────────────────────────────────────────────────────────────
if (require.main === module) {
  const { sub, flags, positional } = parseArgs(process.argv)
  if (sub === 'init')         cmdInit(flags)
  else if (sub === 'mint')    cmdMint(flags)
  else if (sub === 'verify')  cmdVerify(positional)
  else {
    console.error('usage:')
    console.error('  node keygen.js init [--force]')
    console.error('  node keygen.js mint --email <addr> [--days <n>] [--tier <name>] [--note <text>]')
    console.error('  node keygen.js verify <key>')
    process.exit(2)
  }
}

module.exports = { verifyKey, PREFIX, b64urlEncode, b64urlDecode }
