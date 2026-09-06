import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  testMatch: 'real.spec.ts',
  timeout: 1800000,
  workers: 1,
  use: {
    baseURL: process.env.MOVIE_AGENT_STUDIO_URL || 'http://127.0.0.1:5173',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
  },
});
