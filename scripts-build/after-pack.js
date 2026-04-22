// electron-builder afterPack hook.
//
// Two things codesign on macOS Sequoia (15.x / Darwin 24) hates:
//
//   1. Extended attributes / resource forks / AppleDouble / FinderInfo.
//      These need to be stripped. `xattr -cr` + `ditto --norsrc --noextattr
//      --noacl` covers visible cases.
//
//   2. The AD-HOC SIGNATURES that Electron's prebuilt helper binaries
//      already carry. Codesign 15.x is stricter about resigning pre-signed
//      helpers: it sometimes reads leftover signature metadata and
//      complains with "resource fork, Finder information, or similar
//      detritus not allowed" — even when the file has zero xattrs and zero
//      FinderInfo. The fix is to `codesign --remove-signature` every
//      binary BEFORE electron-builder runs its own signing pass.
//
// Order of operations:
//   1. Delete AppleDouble sidecars + .DS_Store.
//   2. xattr -cr (strip extended attributes).
//   3. ditto --norsrc --noextattr --noacl round-trip (strips forks/ACLs).
//   4. codesign --remove-signature on every Mach-O binary (strips adhoc sigs).
//   5. xattr -cr again (removing signatures sometimes leaves artifacts).
//   6. Diagnostic dump.

const { execSync } = require('child_process')
const path = require('path')
const fs = require('fs')
const os = require('os')

function run(cmd) {
  return execSync(cmd, { stdio: 'inherit' })
}

function runQuiet(cmd) {
  try {
    return execSync(cmd, { stdio: ['ignore', 'pipe', 'pipe'] }).toString()
  } catch (e) {
    return ''
  }
}

function dumpXattrs(label, filePath) {
  if (!fs.existsSync(filePath)) return
  const xattrs = runQuiet(`xattr -l "${filePath}"`).trim()
  const info = runQuiet(`GetFileInfo -aE "${filePath}" 2>/dev/null`).trim()
  const sig = runQuiet(`codesign -dv "${filePath}" 2>&1 | head -3`).trim()
  console.log(`[afterPack] ${label}:`)
  console.log(`    xattrs: ${xattrs || '(none)'}`)
  console.log(`    FinderInfo: ${info || '0'}`)
  console.log(`    codesign: ${sig.split('\n').join(' | ') || '(unsigned)'}`)
}

exports.default = async function afterPack(context) {
  const appName = context.packager.appInfo.productFilename + '.app'
  const appPath = path.join(context.appOutDir, appName)

  if (!fs.existsSync(appPath)) {
    console.warn('[afterPack] .app not found at', appPath, '— skipping')
    return
  }

  const mainExec  = path.join(appPath, 'Contents/MacOS/WillettBot')
  const gpuHelper = path.join(appPath, 'Contents/Frameworks/WillettBot Helper (GPU).app/Contents/MacOS/WillettBot Helper (GPU)')

  console.log('[afterPack] Scrubbing:', appPath)

  try {
    // 1. AppleDouble / .DS_Store cleanup.
    try { run(`dot_clean -m "${appPath}"`) } catch (_) {}
    run(`find "${appPath}" -name '._*' -type f -delete`)
    run(`find "${appPath}" -name '.DS_Store' -type f -delete`)

    // 2. xattr -cr on the whole tree.
    run(`xattr -cr "${appPath}"`)

    // 3. ditto round-trip (strips resource forks and ACLs).
    const tmpDir = path.join(os.tmpdir(), 'wb-scrub-' + Date.now())
    console.log('[afterPack] ditto scrub ->', tmpDir)
    run(`ditto --norsrc --noextattr --noacl "${appPath}" "${tmpDir}"`)
    run(`rm -rf "${appPath}"`)
    run(`ditto --norsrc --noextattr --noacl "${tmpDir}" "${appPath}"`)
    run(`rm -rf "${tmpDir}"`)

    console.log('[afterPack] === post-ditto (pre-sig-strip) ===')
    dumpXattrs('main exec', mainExec)
    dumpXattrs('GPU helper', gpuHelper)

    // 4. Strip existing ad-hoc signatures from every Mach-O binary.
    //    Codesign 15.x rejects pre-signed helpers in "resigning" flows.
    //    Removing the old signature first gives us a clean slate.
    console.log('[afterPack] Stripping existing signatures from all binaries...')
    const stripScript = `
      set +e
      find "${appPath}" -type f -print0 | while IFS= read -r -d '' f; do
        # Only touch Mach-O binaries (skip plain text/images/etc).
        if file -b "$f" 2>/dev/null | grep -qE 'Mach-O|universal binary'; then
          codesign --remove-signature "$f" 2>/dev/null
        fi
      done
      # Also strip from the .app bundles themselves (helpers are bundles).
      find "${appPath}" -name '*.app' -print0 | while IFS= read -r -d '' b; do
        codesign --remove-signature "$b" 2>/dev/null
      done
      codesign --remove-signature "${appPath}" 2>/dev/null
      true
    `
    execSync(stripScript, { stdio: 'inherit', shell: '/bin/bash' })

    // 5. Final xattr sweep (removing sigs can leave _CodeSignature dirs with xattrs).
    run(`xattr -cr "${appPath}"`)
    run(`find "${appPath}" -name '_CodeSignature' -type d -exec rm -rf {} + 2>/dev/null || true`)
    run(`find "${appPath}" -name 'CodeResources' -type f -delete 2>/dev/null || true`)

    console.log('[afterPack] === post-sig-strip ===')
    dumpXattrs('main exec', mainExec)
    dumpXattrs('GPU helper', gpuHelper)

    console.log('[afterPack] Cleanup complete.')
  } catch (e) {
    console.error('[afterPack] Cleanup failed:', e.message)
    throw e
  }
}
