import { test, expect } from '@playwright/test';

test('opt-in real brain through Studio, with human approval and Mock media', async ({ page }) => {
  test.skip(
    process.env.MOVIE_AGENT_RUN_P2B_REAL !== '1',
    'Explicitly opt in; starts one new real reasoning production.',
  );
  await page.goto('/');
  await page.getByLabel('界面语言 / Interface language').selectOption('en');
  await page
    .getByLabel('Your story')
    .fill(
      'In a quiet spacecraft cabin, one archivist hears a brief signal from tomorrow, checks the recorder, then chooses to answer. One location, one character, a simple visible action, no complex props.',
    );
  await page.getByRole('button', { name: 'Start Film', exact: true }).click();
  await expect(page).toHaveURL(/project=project_/);
  await expect(page.locator('.studio-node')).not.toHaveCount(0);
  for (let gate = 0; gate < 3; gate++) {
    await expect(page.getByRole('button', { name: 'Approve & resume' })).toBeVisible({
      timeout: 600000,
    });
    const reviewId = await page.locator('.review-card').getAttribute('data-review-id');
    await page.getByRole('button', { name: 'Approve & resume' }).click();
    await expect(
      page
        .locator(`[data-review-id="${reviewId}"]`)
        .getByRole('button', { name: 'Approve & resume' }),
    ).not.toBeVisible();
  }
  await expect(page.locator('.stage-label')).toHaveText('Workflow completed', { timeout: 600000 });
  await expect(page.locator('.react-flow__node-shot')).not.toHaveCount(0);
  await page.getByRole('tab', { name: 'Models', exact: true }).click();
  await expect(page.getByText('Deterministic test reasoning')).not.toBeVisible();
  await expect(page.locator('.model-row .mock')).toHaveCount(6);
});
