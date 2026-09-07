import { expect, test } from '@playwright/test';

test('opt-in existing real H3 project plays video/native audio and exposes its execution graph', async ({
  page,
}) => {
  test.skip(
    process.env.MOVIE_AGENT_RUN_H3_BROWSER !== '1',
    'Explicit opt-in only; verifies an existing real H3 acceptance without generating again.',
  );
  const projectId = process.env.MOVIE_AGENT_H3_PREVIEW_PROJECT;
  expect(projectId, 'MOVIE_AGENT_H3_PREVIEW_PROJECT is required').toBeTruthy();
  const sparkRequests: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes(':8188')) sparkRequests.push(request.url());
  });

  const snapshotResponse = await page.request.get(`/api/projects/${projectId}/studio`);
  expect(snapshotResponse.ok()).toBe(true);
  const snapshot = await snapshotResponse.json();
  const h3Artifacts = snapshot.artifacts.filter(
    (artifact: { provenance?: { provider_id?: string | null } }) =>
      artifact.provenance?.provider_id === 'comfyui-video',
  );
  const video = h3Artifacts.find(
    (artifact: { artifact_type: string }) => artifact.artifact_type === 'video',
  );
  const audio = h3Artifacts.find(
    (artifact: { artifact_type: string }) => artifact.artifact_type === 'audio',
  );
  expect(video).toBeTruthy();
  expect(audio).toBeTruthy();
  expect(video.metadata.mock).toBe(false);
  expect(audio.metadata.mock).toBe(false);
  expect(snapshot.provider_execution_graphs[0].nodes).toHaveLength(18);

  await page.goto(`/?project=${projectId}`);
  await page.getByLabel('界面语言 / Interface language').selectOption('en');
  const videoNode = page.locator('.react-flow__node[data-id="shot_production"]');
  await expect(
    videoNode.getByRole('button', { name: 'Expand ComfyUI execution graph' }),
  ).toBeAttached();
  await videoNode
    .getByRole('button', { name: 'Expand ComfyUI execution graph' })
    .evaluate((button: HTMLButtonElement) => button.click());
  await expect(videoNode.locator('.execution-node')).toHaveCount(18);
  await expect(videoNode.locator('.execution-succeeded')).toHaveCount(18);

  await page.getByRole('button', { name: 'Open inspector', exact: true }).click();
  await page.getByRole('tab', { name: 'Artifacts', exact: true }).click();
  for (const artifact of [video, audio]) {
    const card = page.locator('.artifact-card').filter({
      has: page.getByText(artifact.artifact_id, { exact: true }),
    });
    await card.locator('summary').click();
    await expect(card.locator('.mock')).toHaveCount(0);
    const media = card.locator(artifact.artifact_type === 'video' ? 'video' : 'audio');
    await expect(media).toBeVisible();
    await expect
      .poll(() => media.evaluate((element: HTMLMediaElement) => element.readyState))
      .toBeGreaterThanOrEqual(1);
    expect(await media.evaluate((element: HTMLMediaElement) => element.duration)).toBeGreaterThan(
      0,
    );
  }
  expect(sparkRequests).toEqual([]);
  await page.screenshot({
    path: `../workspace/flux-agent-acceptance-20260907/${projectId}/studio-h3-preview.png`,
    fullPage: true,
  });
});
