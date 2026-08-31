import { defineConfig, devices } from '@playwright/test';

const runtimeEnv = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {};
const apiPort = runtimeEnv.E2E_API_PORT ?? '18000';
const webPort = runtimeEnv.E2E_WEB_PORT ?? '15173';
const browserChannel = runtimeEnv.E2E_BROWSER_CHANNEL;

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  // Tests launch a separate, headless browser context. They never attach to a
  // user's open browser window; E2E_HEADED=1 is only for a deliberate local
  // debugging session.
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    headless: runtimeEnv.E2E_HEADED !== '1',
    trace: 'retain-on-failure',
  },
  webServer: [
    // Replace the shell process with the actual server. Without ``exec``,
    // Playwright stops only that shell and leaves uvicorn/Vite orphaned on
    // their ports, making the next headless run fail before its tests start.
    { command: `exec env GREEN_ATLAS_DB_PATH=/private/tmp/green-atlas-e2e-${apiPort}-$$.sqlite3 ../api/.venv/bin/python -m uvicorn app.main:app --app-dir ../api --port ${apiPort}`, port: Number(apiPort), reuseExistingServer: false },
    // Force dependency prebundling because a concurrently used local Vite
    // server may have cached @green/api-client with port 8000. Reusing that
    // cache would make the browser and Playwright API fixture mutate two
    // different project repositories while appearing to use the same ID.
    { command: `exec env VITE_API_URL=http://127.0.0.1:${apiPort} ./node_modules/.bin/vite --force --host 127.0.0.1 --port ${webPort}`, port: Number(webPort), reuseExistingServer: false },
  ],
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], ...(browserChannel ? { channel: browserChannel } : {}) } }],
});
