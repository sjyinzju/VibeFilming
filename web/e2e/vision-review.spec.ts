import { test, expect } from '@playwright/test';

test('real Scene 01 bytes → leased VLM → durable events → Studio review → refresh', async ({
  page,
  request,
}) => {
  test.skip(
    process.env.MOVIE_AGENT_RUN_VISION_INTEGRATION !== '1',
    'Real VLM inspection is opt-in',
  );
  const projectId = 'project_3effeb45f3844bf89c2c96e83770114b';
  const direct: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes(':8001')) direct.push(r.url());
  });
  await page.goto(`/?project=${projectId}`);
  await page.evaluate(() => localStorage.setItem('studio:v1:language', JSON.stringify('en')));
  await page.reload();
  const before = await (await request.get(`/api/projects/${projectId}/studio`)).json();
  const response = await request.post('/api/p4b/inspect-existing-video');
  expect(response.ok()).toBeTruthy();
  await expect
    .poll(
      async () => {
        const report = await (await request.get('/api/p4b/acceptance')).json();
        if (report.status === 'failed') throw new Error(report.error_type);
        return report.provider;
      },
      { timeout: 1100000, intervals: [2000, 5000] },
    )
    .toBe('qwen3_vl');
  const after = await (await request.get(`/api/projects/${projectId}/studio`)).json();
  const inspection = [...after.media_inspections]
    .reverse()
    .find((i: any) => i.provider_id === 'qwen3_vl');
  expect(inspection.target_artifact_version).toBe(1);
  expect(
    after.jobs
      .filter((j: any) => j.task !== 'vision')
      .map((j: any) => j.job_id)
      .sort(),
  ).toEqual(
    before.jobs
      .filter((j: any) => j.task !== 'vision')
      .map((j: any) => j.job_id)
      .sort(),
  );
  const replay = await (await request.get(`/api/projects/${projectId}/events?follow=false`)).text();
  expect(replay).toContain('media_evaluation_completed');
  expect(replay).toContain('resource_lease_acquired');
  expect(replay).toContain(inspection.result_id);
  await page.getByRole('button', { name: 'Fit view', exact: true }).first().click();
  await page.getByText('Visual/Semantic Critic', { exact: true }).first().click();
  const panel = page.locator(`[data-inspection-id="${inspection.result_id}"]`).first();
  await expect(panel.locator('.review-provider')).toContainText('Qwen3-VL');
  await expect(panel.getByText('MOCK', { exact: true })).toHaveCount(0);
  await expect(panel.getByText('movie-agent-vision', { exact: true })).toBeVisible();
  await expect(panel.getByRole('meter')).toHaveCount(inspection.scores.length);
  await expect(panel.getByText(inspection.summary, { exact: true })).toBeVisible();
  if (inspection.evidence.length)
    await panel.getByText('Observed evidence', { exact: true }).click();
  for (const evidence of inspection.evidence)
    await expect(panel.getByText(evidence, { exact: true }).first()).toBeVisible();
  for (const issue of inspection.issues) {
    await expect(panel.getByText(issue.message, { exact: true })).toBeVisible();
    for (const evidence of issue.evidence)
      await expect(panel.getByText(evidence, { exact: true }).first()).toBeVisible();
    for (const range of issue.time_ranges)
      await expect(
        panel
          .getByRole('button', {
            name: `${range.start_seconds.toFixed(1)}–${range.end_seconds.toFixed(1)}s`,
            exact: true,
          })
          .first(),
      ).toBeVisible();
  }
  const video = panel.locator('video');
  await expect(video).toBeVisible();
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState))
    .toBeGreaterThanOrEqual(1);
  await video.evaluate((v: HTMLVideoElement) => v.play());
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime))
    .toBeGreaterThan(0);
  await page.screenshot({
    path: '../workspace/p4b-vision-acceptance/studio-review.png',
    fullPage: true,
  });
  await page.getByRole('tab', { name: 'Models', exact: true }).click();
  await expect(page.getByText('movie-agent-vlm', { exact: true })).toBeVisible();
  const modelRow = page.locator('.model-row').filter({ hasText: 'movie-agent-vlm' });
  const vlm = after.resource_runtime.services.find((s: any) => s.service_id === 'vlm');
  await expect(modelRow.locator('.badge')).toHaveText(vlm.status.toUpperCase());
  await page.screenshot({ path: '../workspace/p4b-vision-acceptance/studio-models.png', fullPage: true });
  await page.getByRole('tab', { name: 'Node', exact: true }).click();
  await page.reload();
  await page.getByRole('button', { name: 'Fit view', exact: true }).first().click();
  await page.getByText('Visual/Semantic Critic', { exact: true }).first().click();
  await expect(
    page.locator(`[data-inspection-id="${inspection.result_id}"]`).first(),
  ).toBeVisible();
  expect(direct).toEqual([]);
  const proofResponse = await request.post('/api/p4b/human-contract');
  expect(proofResponse.ok()).toBeTruthy();
  const proof = await proofResponse.json();
  expect(proof.review.gate_type).toBe('agent_escalation');
  expect(proof.directive.inspection_result_id).toBe(inspection.result_id);
  expect(proof.directive.target_sha256).toBe(inspection.target_sha256);
  expect(proof.new_inference_jobs).toBe(0);
});
