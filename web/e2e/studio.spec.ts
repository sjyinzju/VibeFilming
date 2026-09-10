import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('studio:v1:language', JSON.stringify('en')));
});

test('quality dashboard exposes pending evidence and filters without inventing acceptance', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Your story').fill('An adult engineer examines a silent machine.');
  await page.getByRole('button', { name: 'Start Film', exact: true }).click();
  await expect(page).toHaveURL(/project=project_/);
  await page.locator('.react-flow__node[data-id="story_gate"]').click({ force: true });
  await page.getByRole('tab', { name: 'Quality', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Quality dashboard' })).toBeVisible();
  await expect(page.getByText('Human aesthetic and listening review pending')).toBeVisible();
  await page.getByLabel('Quality filter').selectOption('human_review');
  await expect(page.getByLabel('Quality filter')).toHaveValue('human_review');
});

test('browser image upload persists, previews, binds, and reaches fake remote unchanged', async ({
  page,
}) => {
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    'base64',
  );
  await page.goto('/');
  await page
    .getByLabel('Your story')
    .fill('An uploaded image guides a quiet science-fiction scene.');
  await page.getByRole('button', { name: 'Advanced' }).click();
  await page
    .locator('summary')
    .filter({ hasText: /^Visual/ })
    .click();
  await page.getByLabel('Upload Project visual references').setInputFiles({
    name: 'browser-reference.png',
    mimeType: 'image/png',
    buffer: png,
  });
  await expect(page.getByText('browser-reference.png')).toBeVisible();
  await expect(page.locator('.reference-card img')).toHaveAttribute(
    'src',
    /\/thumbnail\?draft_id=/,
  );
  await page.reload();
  await page.getByRole('button', { name: 'Advanced' }).click();
  await page
    .locator('summary')
    .filter({ hasText: /^Visual/ })
    .click();
  await expect(page.getByText('browser-reference.png')).toBeVisible();
  await page.getByRole('button', { name: 'Start Film', exact: true }).click();
  await expect(page).toHaveURL(/project=project_/);
  const pid = new URL(page.url()).searchParams.get('project')!;
  const snapshot = await (await page.request.get(`/api/projects/${pid}/studio`)).json();
  expect(snapshot.media_references).toHaveLength(1);
  const reference = snapshot.media_references[0];
  expect(snapshot.project.brief.reference_images).toContain(reference.artifact_id);
  expect(snapshot.creative_hints.media_references[0].artifact_id).toBe(reference.artifact_id);
  const served = await page.request.get(
    `/api/artifacts/${reference.artifact_id}/content?project_id=${pid}`,
  );
  expect(Buffer.compare(await served.body(), png)).toBe(0);

  const transported = await (
    await page.request.post(
      `/api/test/projects/${pid}/reference-transport/${reference.artifact_id}`,
    )
  ).json();
  expect(transported.receipts[0].kind).toBe('image');
  expect(transported.receipts[0].parts[0]).toMatchObject({
    field: 'references',
    filename: 'browser-reference.png',
    mime: 'image/png',
    reference_type: 'style',
  });
  expect(transported.receipts[1].parts.map((part: { field: string }) => part.field)).toEqual([
    'first_frame',
    'last_frame',
    'references',
  ]);
  for (const receipt of transported.receipts)
    for (const part of receipt.parts) expect(part.sha256).toBe(transported.expected_sha256);
});

test('fake ComfyUI events grow a selected video node and remain inspectable after completion', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByLabel('Your story').fill('A camera follows one quiet mechanical gesture.');
  const eventStreamReady = page.waitForResponse(
    (response) => response.url().endsWith('/events') && response.status() === 200,
  );
  await page.getByRole('button', { name: 'Start Film', exact: true }).click();
  await expect(page).toHaveURL(/project=project_/);
  await eventStreamReady;
  const projectId = new URL(page.url()).searchParams.get('project')!;
  const videoNode = page.locator('.react-flow__node[data-id="shot_production"]');
  await expect(videoNode).toHaveCount(1);
  await videoNode.click({ force: true });
  await expect(videoNode.locator('.execution-node')).toHaveCount(0);

  const start = await page.request.post(`/api/test/projects/${projectId}/comfyui-execution/start`);
  expect(start.ok()).toBe(true);
  expect((await start.json()).node_count).toBe(18);
  await expect(videoNode.locator('.execution-graph-panel')).toBeAttached();
  await expect(videoNode.locator('.execution-node')).toHaveCount(18);
  await expect(
    videoNode.locator('[aria-label="Load MiniMax H3 FL2VA INT8 ConvRot: running"]'),
  ).toBeAttached();

  await page.request.post(`/api/test/projects/${projectId}/comfyui-execution/sampling`);
  await expect(
    videoNode.locator('[aria-label="Sample video and audio latents: running 43%"]'),
  ).toBeAttached();

  await page.request.post(`/api/test/projects/${projectId}/comfyui-execution/success`);
  await expect(videoNode.locator('.execution-succeeded')).toHaveCount(18);
  await videoNode.getByRole('button', { name: 'Collapse ComfyUI execution graph' }).click();
  await expect(videoNode.locator('.execution-node')).toHaveCount(0);

  await page.reload();
  const restoredVideoNode = page.locator('.react-flow__node[data-id="shot_production"]');
  await expect(restoredVideoNode.locator('.execution-node')).toHaveCount(0);
  await restoredVideoNode.getByRole('button', { name: 'Expand ComfyUI execution graph' }).click();
  await expect(restoredVideoNode.locator('.execution-succeeded')).toHaveCount(18);
});

test('story-only production, live canvas, human gates, refresh and Mock completion', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const requests: string[] = [];
  page.on('request', (request) => requests.push(request.url()));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Every film starts somewhere.' })).toBeVisible();
  await page.screenshot({ path: 'test-results/studio-empty.png', fullPage: true });
  await page
    .getByLabel('Your story')
    .fill('An archivist hears a signal from tomorrow and chooses to answer.');
  await page.reload();
  await expect(page.getByLabel('Your story')).toHaveValue(
    'An archivist hears a signal from tomorrow and chooses to answer.',
  );
  await page.getByRole('button', { name: 'Start Film', exact: true }).click();
  await expect(page).toHaveURL(/project=project_/);
  await expect(page.locator('.studio-node')).toHaveCount(21);
  for (let gate = 0; gate < 3; gate++) {
    const approve = page
      .locator('button:enabled')
      .filter({ hasText: 'Approve & resume' });
    await expect(approve).toBeVisible({ timeout: 30000 });
    if (gate === 0) {
      const subject = page.locator('.review-subject');
      await expect(
        subject.getByRole('heading', { name: 'Story proposal', exact: true }),
      ).toBeVisible();
      await expect(subject.getByText('A person chooses truth', { exact: true })).toBeVisible();
      const pid = new URL(page.url()).searchParams.get('project')!;
      const snapshot = await (await page.request.get(`/api/projects/${pid}/studio`)).json();
      const source = snapshot.review_subjects[0].sources[0];
      await subject.getByRole('button', { name: 'Raw JSON', exact: true }).click();
      expect(JSON.parse((await subject.getByTestId('review-output').textContent())!)).toEqual(
        source.role_result.output,
      );
      await subject.getByRole('button', { name: 'Summary', exact: true }).click();
      await subject.getByRole('button', { name: 'View source node' }).click();
      await expect(page.locator('.inspector h2')).toHaveText('Story Planning');
      await expect(
        page.locator(`.react-flow__node[data-id="${source.source_node_id}"]`),
      ).toHaveClass(/selected/);
      await page.reload();
      await expect(
        page.locator('.review-subject').getByText('A person chooses truth', { exact: true }),
      ).toBeVisible();
      await page.getByRole('button', { name: 'Focus current stage' }).click();
      const gateNode = page.locator('.react-flow__node[data-id="story_gate"]');
      await expect(gateNode).toBeVisible();
      const box = (await gateNode.boundingBox())!;
      await page.mouse.move(box.x + box.width / 2, box.y + 35);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + 75, box.y + 70, { steps: 8 });
      await page.mouse.up();
      const position = await gateNode.evaluate((el) => (el as HTMLElement).style.transform);
      await page.screenshot({ path: 'test-results/studio-human-review.png', fullPage: true });
      await page.reload();
      await expect(approve).toBeVisible();
      await expect(
        page.locator('.review-subject').getByText('A person chooses truth', { exact: true }),
      ).toBeVisible();
      await expect
        .poll(() => gateNode.evaluate((el) => (el as HTMLElement).style.transform))
        .toBe(position);
    }
    if (gate === 1) await expect(page.locator('.review-subject')).toContainText('Shot count');
    if (gate === 2) {
      await expect(page.locator('.review-subject .mock')).toBeVisible();
      await expect(page.locator('.review-subject video')).toHaveCount(0);
    }
    const reviewId = await page.locator('.review-card').getAttribute('data-review-id');
    await approve.click();
    await expect(
      page
        .locator(`[data-review-id="${reviewId}"]`)
        .getByRole('button', { name: 'Approve & resume' }),
    ).toBeDisabled();
  }
  await expect(page.locator('.stage-label')).toHaveText('Workflow completed', { timeout: 30000 });
  await expect(page.locator('.react-flow__node-scene')).toHaveCount(1);
  await expect(page.locator('.react-flow__node-shot')).toHaveCount(1);
  await expect(page.locator('.react-flow__node-shot .shot-status')).toContainText('Frames ✓');
  await expect(page.locator('.react-flow__node-shot .shot-status')).toContainText('Video ✓');
  await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  await page.getByRole('tab', { name: 'Models', exact: true }).click();
  await expect(page.getByText('Deterministic test reasoning')).toBeVisible();
  await expect(page.locator('.model-row .mock')).toHaveCount(6);
  await page.getByRole('tab', { name: 'Artifacts', exact: true }).click();
  await expect(page.getByText('final_film · v1', { exact: true })).toBeVisible();
  await page.getByText('final_film · v1', { exact: true }).click();
  await expect(page.locator('.artifact-card video').last()).toBeVisible();
  await expect(
    page.getByText('Playable test media produced by the Mock provider.').last(),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Close inspector' }).click();
  await page.getByRole('button', { name: 'Auto layout', exact: true }).click();
  await page.getByRole('button', { name: 'Focus current stage' }).click();
  await expect(page.locator('.react-flow__node-final')).toBeInViewport();
  await page.screenshot({ path: 'test-results/studio-completed.png', fullPage: true });
  await page.reload();
  await expect(page.locator('.stage-label')).toHaveText('Workflow completed');
  await expect(page.locator('.react-flow__node-shot')).toHaveCount(1);
  expect(requests.some((url) => url.includes('/v1/'))).toBe(false);
  expect(errors).toEqual([]);
});

test('offline SSE reconnect recovers events and newly committed scene/shot data', async ({
  page,
  context,
}) => {
  await page.goto('/');
  await page.getByLabel('Your story').fill('A person hears a signal and makes a choice.');
  await page.getByRole('button', { name: 'Start Film', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve & resume' })).toBeVisible();
  const pid = new URL(page.url()).searchParams.get('project')!;
  const reviews = await (await page.request.get(`/api/projects/${pid}/reviews`)).json();
  await context.setOffline(true);
  await expect(page.locator('.connection')).toHaveText(/Reconnecting|Offline/, { timeout: 15000 });
  await page.request.post(`/api/reviews/${reviews[0].review_id}/resolve`, {
    data: { approved: true },
  });
  await page.request.post(`/api/projects/${pid}/resume`);
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/api/projects/${pid}/studio`)).json()).project.shots.length,
    )
    .toBeGreaterThan(0);
  await context.setOffline(false);
  await expect(page.locator('.connection')).toHaveText('Live', { timeout: 20000 });
  await expect(page.locator('.react-flow__node-shot')).toHaveCount(1);
  await expect(page.locator('.canvas-footer')).toContainText(/Recovered \d+ events/);
  await expect(
    page.getByRole('heading', { name: 'Shot Plan Approval', exact: true }),
  ).toBeVisible();
  await expect(page.locator('.review-subject')).toContainText('Shot count');
});

test('pause at a boundary, resume, and cancel use backend state', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Your story').fill('A person listens to a signal in a room.');
  await page.getByRole('button', { name: 'Start Film', exact: true }).click();
  const pause = page.getByRole('button', { name: 'Pause', exact: true });
  await expect(pause).toBeEnabled();
  await pause.click();
  await expect(page.getByText('Pause requested.', { exact: false })).toBeVisible();
  const resume = page.getByRole('button', { name: 'Resume', exact: true });
  await expect(resume).toBeEnabled({ timeout: 12000 });
  await resume.click();
  await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByText('Local production cancelled', { exact: false })).toBeVisible();
  await expect(resume).toBeDisabled();
  await page.reload();
  await expect(page.locator('.stage-label')).toHaveText('cancelled');
});

test('rejected review retains its subject after reload', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Your story').fill('A signal changes a life.');
  await page.getByRole('button', { name: 'Start Film', exact: true }).click();
  await expect(page.locator('.review-subject')).toContainText('A person chooses truth');
  await page.getByLabel('Your notes').fill('Preserve the open ending.');
  await page.getByRole('button', { name: 'Request revision', exact: true }).click();
  await expect(page.locator('.review-card')).toContainText('Preserve the open ending.');
  await expect(page.locator('.review-subject')).toContainText('A person chooses truth');
  await page.reload();
  await page.getByRole('button', { name: 'Focus current stage' }).click();
  await page.locator('.react-flow__node[data-id="story_gate"]').click();
  await expect(page.locator('.review-subject')).toContainText('A person chooses truth');
  await expect(page.locator('.review-card')).toContainText('Preserve the open ending.');
  await expect(page.getByRole('button', { name: 'Approve & resume' })).toHaveCount(0);
});
