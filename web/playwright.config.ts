import { defineConfig } from '@playwright/test';
const port = Number(process.env.MOVIE_AGENT_STUDIO_TEST_PORT || 5173);
const baseURL = `http://127.0.0.1:${port}`;
export default defineConfig({
  testDir: './e2e',
  testMatch: ['studio.spec.ts', 'locale.spec.ts'],
  timeout: 90000,
  workers: 1,
  use: {
    baseURL,
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'node scripts/test-backend.mjs',
      url: 'http://127.0.0.1:8081/health',
      reuseExistingServer: false,
      timeout: 20000,
    },
    {
      command: `npm run dev -- --port ${port}`,
      url: baseURL,
      env: { MOVIE_AGENT_API_PROXY: 'http://127.0.0.1:8081' },
      reuseExistingServer: false,
    },
  ],
});
