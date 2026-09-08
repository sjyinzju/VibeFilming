import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  testMatch: 'vision-review.spec.ts',
  workers: 1,
  timeout: 1200000,
  use: {
    baseURL: 'http://127.0.0.1:5176',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command:
        '.\\.venv312\\Scripts\\python.exe -m uvicorn tests.p4b_real_server:app --host 127.0.0.1 --port 8084',
      cwd: '..',
      env: { MOVIE_AGENT_RUN_VISION_INTEGRATION: '1' },
      url: 'http://127.0.0.1:8084/health',
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command: 'npm run dev -- --port 5176',
      env: { MOVIE_AGENT_API_PROXY: 'http://127.0.0.1:8084' },
      url: 'http://127.0.0.1:5176',
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
