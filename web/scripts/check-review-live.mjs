// Read-only acceptance against an existing waiting project. Never approves or runs roles.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
const projectId = process.argv[2];
if (!projectId) throw new Error('Supply an existing project ID');
const origin = process.env.MOVIE_AGENT_STUDIO_URL || 'http://127.0.0.1:5173';
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.route('**/api/**', (route) => {
    if (route.request().method() !== 'GET')
      throw new Error('Read-only acceptance forbids commands');
    return route.continue();
  });
  await page.goto(`${origin}/?project=${encodeURIComponent(projectId)}`);
  const response = await page.request.get(
    `${origin}/api/projects/${encodeURIComponent(projectId)}/studio`,
  );
  assert.equal(response.status(), 200);
  const snapshot = await response.json();
  const review = snapshot.reviews.find((r) => r.status === 'pending');
  assert.ok(review);
  const subject = snapshot.review_subjects.find((s) => s.review_id === review.review_id);
  const source = subject.sources[0];
  const card = page.locator(`[data-review-id="${review.review_id}"]`);
  await card.locator('.review-subject').waitFor();
  assert.ok((await card.textContent()).includes(source.content.output.synopsis));
  await card.getByRole('button', { name: '原始 JSON', exact: true }).click();
  assert.deepEqual(
    JSON.parse(await card.getByTestId('review-output').textContent()),
    source.role_result.output,
  );
  await card.getByRole('button', { name: '摘要', exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector('.connection')?.textContent.includes('实时连接'),
    null,
    { timeout: 20000 },
  );
  await page.locator('.inspector-content').evaluate((element) => {
    element.scrollTop = 0;
  });
  await page.screenshot({ path: 'test-results/studio-real-review.png', fullPage: true });
  console.log(
    JSON.stringify({
      review_id: review.review_id,
      source_node_id: source.source_node_id,
      real_output_matches: true,
      commands_sent: 0,
    }),
  );
} finally {
  await browser.close();
}
