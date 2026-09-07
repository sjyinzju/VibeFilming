import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'flux-preview.spec.ts',
  workers: 1,
  timeout: 90000,
  use: {
    baseURL: process.env.MOVIE_AGENT_FLUX_STUDIO_URL || 'http://127.0.0.1:5174',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
  },
});
