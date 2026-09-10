import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  testMatch: 'audio-production.spec.ts',
  workers: 1,
  timeout: 120000,
  use: {
    baseURL: 'http://127.0.0.1:5179',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'node scripts/audio-backend.mjs',
      url: 'http://127.0.0.1:8087/health',
      reuseExistingServer: false,
      timeout: 90000,
    },
    {
      command: 'npm run dev -- --port 5179',
      url: 'http://127.0.0.1:5179',
      env: { MOVIE_AGENT_API_PROXY: 'http://127.0.0.1:8087' },
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
