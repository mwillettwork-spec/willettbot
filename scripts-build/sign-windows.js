// ─────────────────────────────────────────────────────────────────────
// sign-windows.js -- Custom signing function called by electron-builder
// for every Windows binary it produces (.exe installer, embedded helpers,
// etc.). Calls the Microsoft "sign" CLI which talks to Azure Trusted
// Signing using the Service Principal credentials in env vars.
//
// Why this and not vanilla signtool.exe + a .pfx file?
//   - Trusted Signing certs are stored in Azure, not on disk. There is
//     no .pfx to ship.
//   - signtool.exe alone can't talk to Azure; it needs a "dlib" plugin.
//   - Microsoft's `sign` CLI bundles the plugin and handles auth.
//
// Required env vars (provided by .github/workflows/release.yml from
// repo secrets):
//   AZURE_TENANT_ID                 - Directory (tenant) ID
//   AZURE_CLIENT_ID                 - SP application (client) ID
//   AZURE_CLIENT_SECRET             - SP client secret value
//
// Optional env-var overrides (fall back to hardcoded defaults below):
//   AZURE_CODE_SIGNING_ENDPOINT     - default https://eus.codesigning.azure.net/
//   AZURE_CODE_SIGNING_ACCOUNT      - default willettbot
//   AZURE_CERT_PROFILE              - default willettbot-cert
//
// Behaviour when env vars are missing:
//   This function logs a warning and returns without signing. That makes
//   local builds (`npm run dist:win` from a dev machine without secrets)
//   continue to work — they just produce unsigned binaries that show
//   the SmartScreen warning. Only CI builds with secrets injected get
//   real signatures.
// ─────────────────────────────────────────────────────────────────────
const { execSync } = require('child_process');

exports.default = async function (configuration) {
  const filePath = configuration.path;

  // Skip silently if Azure secrets aren't configured (local dev builds).
  const required = ['AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET'];
  for (const k of required) {
    if (!process.env[k]) {
      console.warn(
        `[sign-windows] ${k} not set — skipping (file will be unsigned): ${filePath}`
      );
      return;
    }
  }

  const endpoint = process.env.AZURE_CODE_SIGNING_ENDPOINT
    || 'https://eus.codesigning.azure.net/';
  const account  = process.env.AZURE_CODE_SIGNING_ACCOUNT || 'willettbot';
  const profile  = process.env.AZURE_CERT_PROFILE         || 'willettbot-cert';

  console.log(`[sign-windows] Signing ${filePath} with Azure Trusted Signing...`);
  console.log(`[sign-windows]   endpoint: ${endpoint}`);
  console.log(`[sign-windows]   account:  ${account}`);
  console.log(`[sign-windows]   profile:  ${profile}`);

  // Microsoft `sign` CLI installed via `dotnet tool install --global sign`
  // in the workflow. Microsoft renamed the subcommand and arg flags from
  // `trusted-signing` to `artifact-signing` to match the product rebrand.
  // The artifact-signing subcommand reads AZURE_TENANT_ID / AZURE_CLIENT_ID /
  // AZURE_CLIENT_SECRET from env via Azure DefaultAzureCredential.
  const cmd = [
    'sign code artifact-signing',
    '--artifact-signing-endpoint',            `"${endpoint}"`,
    '--artifact-signing-account',             `"${account}"`,
    '--artifact-signing-certificate-profile', `"${profile}"`,
    `"${filePath}"`,
  ].join(' ');

  // Retry on transient Azure errors. Microsoft Identity occasionally returns
  // 401 invalid_client for valid creds when their auth backend hiccups, and
  // sometimes 429s for throttling when many sign calls fire in succession.
  // Both are transient — same secret works on the next attempt. We retry
  // up to 4 times with exponential backoff (2s, 4s, 8s, 16s) before giving up.
  const MAX_ATTEMPTS = 5;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      execSync(cmd, { stdio: 'inherit', shell: true });
      console.log(`[sign-windows] ✓ Signed: ${filePath}` +
                  (attempt > 1 ? ` (attempt ${attempt})` : ''));
      return;
    } catch (err) {
      if (attempt === MAX_ATTEMPTS) {
        console.error(`[sign-windows] ✗ Failed to sign ${filePath} after ${MAX_ATTEMPTS} attempts: ${err.message}`);
        throw err;
      }
      const backoff = Math.pow(2, attempt) * 1000; // 2s, 4s, 8s, 16s
      console.warn(`[sign-windows] ⚠ Attempt ${attempt} failed for ${filePath}, retrying in ${backoff/1000}s...`);
      await sleep(backoff);
    }
  }
};
