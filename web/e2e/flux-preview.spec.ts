import { test, expect } from '@playwright/test';

test('opt-in existing real FLUX project renders registered PNGs in Studio', async ({ page }) => {
  const projectId = process.env.MOVIE_AGENT_FLUX_PREVIEW_PROJECT;
  test.skip(
    !projectId,
    'Provide an existing real FLUX acceptance project; never starts generation.',
  );
  const snapshotResponse = await page.request.get(`/api/projects/${projectId}/studio`);
  expect(snapshotResponse.ok()).toBe(true);
  const snapshot = await snapshotResponse.json();
  expect(snapshot.media_provider_ids).toContain('flux_direct');
  expect(snapshot.media_provider_ids).not.toContain('mock-image');
  const frames = snapshot.artifacts.filter(
    (item: { artifact_type: string }) => item.artifact_type === 'frame',
  );
  expect(frames.length).toBeGreaterThanOrEqual(2);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`/?project=${projectId}`);
  await page.getByLabel('界面语言 / Interface language').selectOption('en');
  await expect(page.locator('.stage-label')).toHaveText('Workflow completed');
  await page.getByRole('button', { name: 'Open inspector', exact: true }).click();
  await page.getByRole('tab', { name: 'Models', exact: true }).click();
  await expect(page.locator('.model-row .mock')).toHaveCount(4);
  await expect(page.locator('.model-row').filter({ hasText: 'flux_direct' })).toHaveCount(2);
  await page.getByRole('tab', { name: 'Artifacts', exact: true }).click();
  for (const frame of frames) {
    expect(frame.provenance.provider_id).toBe('flux_direct');
    const card = page
      .locator('.artifact-card')
      .filter({ has: page.getByText(frame.artifact_id, { exact: true }) });
    await card.locator('summary').click();
    const img = card.getByRole('img', { name: `${frame.artifact_id} preview` });
    await expect(img).toBeVisible();
    await expect
      .poll(() => img.evaluate((element: HTMLImageElement) => element.naturalWidth))
      .toBe(1024);
    expect(await img.evaluate((element: HTMLImageElement) => element.naturalHeight)).toBe(576);
    await expect(card.locator('.mock')).toHaveCount(0);
  }
  await page.screenshot({
    path: `../workspace/flux-agent-acceptance-20260907/${projectId}/studio-flux-preview.png`,
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
