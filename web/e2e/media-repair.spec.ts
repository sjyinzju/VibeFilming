import { test, expect } from '@playwright/test';

test('human media review → custom repair → new video version → reinspection → refresh', async ({
  page,
  request,
}) => {
  await page.addInitScript(() => localStorage.setItem('studio:v1:language', JSON.stringify('en')));
  const created = await request.post('/api/test/media-project');
  expect(created.ok()).toBeTruthy();
  const { project_id: pid } = await created.json();
  const before = await (await request.get(`/api/projects/${pid}/studio`)).json();
  const inspection = before.media_inspections.find((i: any) => i.decision === 'human_review');
  expect(inspection).toBeTruthy();
  await page.goto(`/?project=${pid}`);
  const panel = page.locator(`[data-inspection-id="${inspection.result_id}"]`).first();
  await expect(panel).toBeVisible();
  await expect(panel.getByText('WAITING_HUMAN', { exact: true })).toBeVisible();
  await expect(panel.getByRole('meter').first()).toBeVisible();
  await expect(panel.getByText('Cup remains on the table.', { exact: true })).toBeVisible();
  await expect(panel.getByRole('button', { name: '1.0–2.0s', exact: true })).toBeVisible();
  for (const name of [
    'Keep current version',
    'Apply AI repair',
    'Regenerate video',
    'Suggest changes',
  ])
    await expect(panel.getByRole('button', { name, exact: true })).toBeEnabled();
  await page.reload();
  await expect(panel).toBeVisible();
  await panel.getByRole('button', { name: 'Suggest changes', exact: true }).click();
  const feedback = '  Preserve the pause.\nComplete the existing cup lift.  ';
  await panel.getByLabel('Your feedback', { exact: true }).fill(feedback);
  await panel.getByLabel('Preserve', { exact: true }).fill('Identity and composition');
  await page.screenshot({
    path: '../workspace/p4b-vision-acceptance/fake-human-review.png',
    fullPage: true,
  });
  await panel.getByRole('button', { name: 'Submit media feedback', exact: true }).click();
  await expect
    .poll(async () => (await (await request.get(`/api/projects/${pid}/studio`)).json()).status)
    .toBe('completed');
  const after = await (await request.get(`/api/projects/${pid}/studio`)).json();
  const revised = after.media_inspections.find(
    (i: any) =>
      i.target_artifact_id === inspection.target_artifact_id && i.target_artifact_version === 2,
  );
  expect(revised.decision).toBe('pass');
  expect(after.human_media_directives[0].feedback).toBe(feedback);
  expect(after.project.story_bible).toEqual(before.project.story_bible);
  expect(
    after.artifacts.filter((a: any) => a.artifact_id === inspection.target_artifact_id),
  ).toHaveLength(2);
  const events = await (await request.get(`/api/projects/${pid}/events?follow=false`)).text();
  for (const type of [
    'human_media_directive_created',
    'media_repair_started',
    'media_repair_completed',
    'media_evaluation_completed',
  ])
    expect(events).toContain(type);
  await page.reload();
  await page.getByRole('button', { name: 'Fit view', exact: true }).first().click();
  await page.getByText('Visual/Semantic Critic', { exact: true }).click();
  await expect(page.locator(`[data-inspection-id="${revised.result_id}"]`).first()).toBeVisible();
});
