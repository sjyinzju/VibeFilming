// Read-only live UI verification; no production commands or inference are submitted.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';

const project = 'project_3effeb45f3844bf89c2c96e83770114b';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:5174/?project=${project}`);
  await page.getByLabel('界面语言 / Interface language').selectOption('en');
  await page.getByRole('button', { name: 'Open inspector', exact: true }).click();
  await page.getByRole('tab', { name: 'Models', exact: true }).click();
  const runtime = page.getByRole('region', { name: 'Resource runtime', exact: true });
  await runtime.waitFor({ state: 'visible' });
  await page.getByText('Live', { exact: true }).waitFor({ state: 'visible', timeout: 15000 });
  assert.match(await runtime.innerText(), /Unified memory/);
  for (const service of ['qwen', 'flux', 'comfyui']) {
    assert.match(await runtime.innerText(), new RegExp(service));
  }
  await runtime.getByText('Recent scheduler decisions', { exact: true }).click();
  await page.waitForFunction(() =>
    document
      .querySelector('[aria-label="Resource runtime"] pre')
      ?.textContent.includes('drain/stop:flux'),
  );
  await page.screenshot({ path: '../workspace/_resource_runtime/resource-ui.png', fullPage: true });
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      passed: true,
      project,
      services: 3,
      decisionsVisible: true,
      screenshot: 'workspace/_resource_runtime/resource-ui.png',
      browserErrors: errors,
    }),
  );
} finally {
  await browser.close();
}
