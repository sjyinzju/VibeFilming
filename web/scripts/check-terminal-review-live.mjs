// Read-only acceptance of a repaired real scene at the shot review gate.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
const projectId = process.argv[2];
if (!projectId) throw new Error('Supply an existing project ID');
const origin = process.env.MOVIE_AGENT_STUDIO_URL || 'http://127.0.0.1:5173';
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.route('**/api/**', route => {
    assert.equal(route.request().method(), 'GET', 'Read-only check forbids commands');
    return route.continue();
  });
  const snapshot = await (await page.request.get(`${origin}/api/projects/${encodeURIComponent(projectId)}/studio`)).json();
  const review = snapshot.reviews.find(r => r.status === 'pending' && r.gate_type === 'shot_plan_approval');
  assert.ok(review);
  const subject = snapshot.review_subjects.find(s => s.review_id === review.review_id);
  assert.equal(subject.subject_type, 'ShotPlan');
  await page.goto(`${origin}/?project=${encodeURIComponent(projectId)}`);
  const card = page.locator(`[data-review-id="${review.review_id}"]`);
  await card.locator('.review-subject').waitFor();
  await card.getByRole('button', { name: '原始 JSON', exact: true }).click();
  const outputs = card.getByTestId('review-output');
  assert.equal(await outputs.count(), subject.sources.length);
  for (const [index, source] of subject.sources.entries()) {
    assert.deepEqual(JSON.parse(await outputs.nth(index).textContent()), source.role_result.output);
  }
  await card.getByRole('button', { name: '摘要', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.connection')?.textContent.includes('实时连接'),
    null, { timeout: 20000 });
  await page.screenshot({ path: 'test-results/studio-terminal-review.png', fullPage: true });
  console.log(JSON.stringify({review_id: review.review_id, subjects: subject.sources.length,
    auto_opened: true, real_outputs_match: true, commands_sent: 0}));
} finally {
  await browser.close();
}
