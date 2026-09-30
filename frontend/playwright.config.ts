import { defineConfig, devices } from '@playwright/test'

// The frontend's first committed Playwright suite (frontend/README.md
// previously documented "No frontend unit-test framework is set up
// yet" -- every prior Playwright check in this repo's history was
// ad-hoc, manual browser driving during development, never a
// committed spec). Points at the pre-installed Chromium this
// environment ships (PLAYWRIGHT_BROWSERS_PATH) rather than
// downloading a browser, and boots the real Vite dev server against
// whatever backend is already running on :8000 -- these specs drive
// real API calls through a real database, not a mocked one, matching
// how every other verification in this codebase already works.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  timeout: 30_000,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
          args: ['--no-sandbox'],
        },
      },
    },
  ],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5173',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 30_000,
  },
})
