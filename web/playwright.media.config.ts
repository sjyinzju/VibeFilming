import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'media-repair.spec.ts',
  workers: 1,
  timeout: 90000,
  use: {
    baseURL: 'http://127.0.0.1:5177',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command:
        '.\\.venv312\\Scripts\\python.exe -m uvicorn tests.p4b_fake_server:app --host 127.0.0.1 --port 8085',
      cwd: '..',
      url: 'http://127.0.0.1:8085/health',
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command: 'npm run dev -- --port 5177',
      env: { MOVIE_AGENT_API_PROXY: 'http://127.0.0.1:8085' },
      url: 'http://127.0.0.1:5177',
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
