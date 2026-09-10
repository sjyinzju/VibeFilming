import { defineConfig } from '@playwright/test';
const real = process.env.MOVIE_AGENT_RUN_POST_INTEGRATION === '1';
export default defineConfig({
  testDir: './e2e',
  testMatch: 'post-export.spec.ts',
  workers: 1,
  timeout: 900000,
  use: {
    baseURL: 'http://127.0.0.1:5178',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'node scripts/post-backend.mjs',
      url: 'http://127.0.0.1:8086/health',
      reuseExistingServer: false,
      timeout: 30000,
      env: { MOVIE_AGENT_RUN_POST_INTEGRATION: real ? '1' : '0' },
    },
    {
      command: 'npm run dev -- --port 5178',
      url: 'http://127.0.0.1:5178',
      env: { MOVIE_AGENT_API_PROXY: 'http://127.0.0.1:8086' },
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
