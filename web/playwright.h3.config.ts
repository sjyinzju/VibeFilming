import { defineConfig } from '@playwright/test';

const projectRoot = process.env.MOVIE_AGENT_H3_PROJECT_ROOT;
const workspace = projectRoot
  ? projectRoot.replace(/[\\/]project_[^\\/]+$/, '')
  : 'workspace/flux-agent-acceptance-20260907';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'h3-preview.spec.ts',
  workers: 1,
  timeout: 120000,
  use: {
    baseURL: 'http://127.0.0.1:5175',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command:
        '.\\.venv312\\Scripts\\python.exe -m uvicorn movie_agent.api.app:create_app --factory --host 127.0.0.1 --port 8083 --workers 1',
      cwd: '..',
      env: { MOVIE_AGENT_WORKSPACE: workspace },
      url: 'http://127.0.0.1:8083/health',
      reuseExistingServer: false,
      timeout: 20000,
    },
    {
      command: 'npm run dev -- --port 5175',
      env: { MOVIE_AGENT_API_PROXY: 'http://127.0.0.1:8083' },
      url: 'http://127.0.0.1:5175',
      reuseExistingServer: false,
      timeout: 20000,
    },
  ],
});
