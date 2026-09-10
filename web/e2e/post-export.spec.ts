import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

test('existing media → real rough-cut gate → final preview, immutable download and refresh', async ({
  page,
  request,
}) => {
  const { project_id: pid } = await (await request.get('/api/p4c/project')).json();
  const prefix = `/api/projects/${pid}`;
  const before = await (await request.get(`${prefix}/studio`)).json();
  const forbidden: string[] = [];
  page.on('request', (r) => {
    if (/:(8001|8188|9001)\b|file:\/\//.test(r.url())) forbidden.push(r.url());
  });
  const begin = await request.post('/api/p4c/start');
  expect(begin.ok()).toBeTruthy();
  await expect
    .poll(async () => (await (await request.get(`${prefix}/studio`)).json()).status, {
      timeout: 800000,
      intervals: [1000, 2000],
    })
    .toMatch(/waiting_human|completed/);
  await page.goto(`/?project=${pid}`);
  await page.evaluate(() => localStorage.setItem('studio:v1:language', JSON.stringify('en')));
  await page.reload();
  await page.getByRole('button', { name: 'Fit view', exact: true }).first().click();
  await page.getByText('Final Cut Approval', { exact: true }).first().click();
  const rough = page.getByTestId('rough-cut').first();
  await expect(rough).toBeVisible();
  await expect(rough.getByText('MOCK', { exact: true })).toHaveCount(0);
  await expect
    .poll(() => rough.locator('video').evaluate((v: HTMLVideoElement) => v.readyState))
    .toBeGreaterThanOrEqual(1);
  await rough.locator('video').evaluate((v: HTMLVideoElement) => {
    v.currentTime = 0.5;
    return v.play();
  });
  await expect
    .poll(() => rough.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime))
    .toBeGreaterThan(0.5);
  let snapshot = await (await request.get(`${prefix}/studio`)).json();
  if (snapshot.status === 'waiting_human') {
    const review = snapshot.reviews.find(
      (r: any) => r.node_id === 'final_gate' && !r.superseded_at,
    );
    await page
      .locator(`[data-review-id="${review.review_id}"]`)
      .getByRole('button', { name: 'Approve & resume' })
      .click();
    await expect
      .poll(async () => (await (await request.get(`${prefix}/studio`)).json()).status, {
        timeout: 800000,
        intervals: [1000, 2000],
      })
      .toBe('completed');
  }
  await page.getByText('Final Render', { exact: true }).first().click();
  const final = page.getByTestId('final-film').first();
  await expect(final).toBeVisible();
  await expect
    .poll(() => final.locator('video').evaluate((v: HTMLVideoElement) => v.readyState))
    .toBeGreaterThanOrEqual(1);
  await final.locator('video').evaluate((v: HTMLVideoElement) => {
    v.currentTime = 1;
    return v.play();
  });
  await expect
    .poll(() => final.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime))
    .toBeGreaterThan(1);
  const downloading = page.waitForEvent('download');
  await final.getByRole('link', { name: 'Download MP4', exact: true }).click();
  const download = await downloading;
  const bytes = await readFile((await download.path())!);
  snapshot = await (await request.get(`${prefix}/studio`)).json();
  const artifact = snapshot.artifacts.find(
    (a: any) => a.artifact_id === 'final_film' && a.selected,
  );
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(artifact.metadata.sha256);
  expect(
    snapshot.jobs
      .filter((j: any) => j.task !== 'post')
      .map((j: any) => j.job_id)
      .sort(),
  ).toEqual(
    before.jobs
      .filter((j: any) => j.task !== 'post')
      .map((j: any) => j.job_id)
      .sort(),
  );
  expect(/file:\/\/|\b[A-Za-z]:[\\/]/.test(JSON.stringify(snapshot))).toBe(false);
  expect(forbidden).toEqual([]);
  await page.reload();
  await page.getByText('Final Render', { exact: true }).first().click();
  await expect(page.getByTestId('final-film').first()).toBeVisible();
  await expect(
    page.getByTestId('final-film').first().getByRole('link', { name: 'Download Render Manifest' }),
  ).toBeVisible();
  const refreshedVideo = page.getByTestId('final-film').first().locator('video');
  await expect
    .poll(() => refreshedVideo.evaluate((v: HTMLVideoElement) => v.readyState))
    .toBeGreaterThanOrEqual(1);
  await refreshedVideo.evaluate((v: HTMLVideoElement) => {
    v.currentTime = Math.min(7.5, v.duration / 2);
  });
  await expect
    .poll(() => refreshedVideo.evaluate((v: HTMLVideoElement) => !v.seeking && v.readyState >= 2))
    .toBe(true);
  await page.screenshot({
    path:
      process.env.MOVIE_AGENT_RUN_POST_INTEGRATION === '1'
        ? '../workspace/p4c-post-acceptance/studio-final.png'
        : '../workspace/p4c-post-e2e/studio-final.png',
  });
});
