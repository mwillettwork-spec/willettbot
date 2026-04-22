#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Downloads a portable Python (python-build-standalone) and pre-installs
# WillettBot's runtime deps (pyautogui, pynput) into it. The resulting
# bundled-python/python/ tree is packaged into the .app by electron-builder
# via extraResources, so end users get a fully-working Python environment
# with ZERO setup — no pip, no venv, no OS Python required on their machine.
#
# Run automatically before `npm run dist`. To force a rebuild, delete the
# bundled-python/ folder and re-run.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

BUNDLE_DIR="bundled-python"
PY_VERSION="3.12.7"
PBS_TAG="20241008"        # python-build-standalone release tag (update periodically)
ARCH="x86_64"             # matches our x64 DMG target; runs on Intel natively + Apple Silicon via Rosetta

TARBALL_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PY_VERSION}+${PBS_TAG}-${ARCH}-apple-darwin-install_only.tar.gz"

# Idempotency: skip if already prepared. Delete bundled-python/ to force rebuild.
if [ -f "${BUNDLE_DIR}/python/bin/python3" ] && [ -d "${BUNDLE_DIR}/python/lib/python3.12/site-packages/pyautogui" ]; then
  echo "[prepare-python] Bundle already exists at ${BUNDLE_DIR}/python — skipping."
  echo "[prepare-python] (Delete ${BUNDLE_DIR}/ to force a fresh download.)"
  exit 0
fi

mkdir -p "${BUNDLE_DIR}"
rm -rf "${BUNDLE_DIR}/python"

echo "[prepare-python] Downloading Python ${PY_VERSION} (${ARCH}, ~40MB)..."
curl -L --fail --progress-bar "${TARBALL_URL}" -o "${BUNDLE_DIR}/python.tar.gz"

echo "[prepare-python] Extracting..."
tar -xzf "${BUNDLE_DIR}/python.tar.gz" -C "${BUNDLE_DIR}"
rm "${BUNDLE_DIR}/python.tar.gz"

# python-build-standalone install_only tarball extracts to "python/..."
if [ ! -f "${BUNDLE_DIR}/python/bin/python3" ]; then
  echo "[prepare-python] ERROR: expected ${BUNDLE_DIR}/python/bin/python3 after extract."
  ls "${BUNDLE_DIR}"
  exit 1
fi

echo "[prepare-python] Installing pyautogui + pynput into bundled Python..."
"${BUNDLE_DIR}/python/bin/python3" -m pip install --quiet --disable-pip-version-check \
  pyautogui pynput

echo "[prepare-python] Done. Final size:"
du -sh "${BUNDLE_DIR}/python"
echo "[prepare-python] Bundled Python ready at ${BUNDLE_DIR}/python/bin/python3"
