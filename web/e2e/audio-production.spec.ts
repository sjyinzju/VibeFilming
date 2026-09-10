import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';

test('voice and dialogue play in browser; three stems, SRT and final download keep exact hashes', async ({
  page,
}) => {
  const info = await (await page.request.get('/api/p4d/project')).json();
  expect(info.offline_fixture).toBe(true);
  await page.addInitScript(() => localStorage.setItem('studio:v1:language', JSON.stringify('en')));
  await page.goto(`/?project=${info.project_id}`);
  await page.getByRole('button', { name: 'Fit view', exact: true }).first().click();
  await page.getByText('Final Render', { exact: true }).first().click();
  await page.getByRole('tab', { name: 'Audio', exact: true }).click();
  const panel = page.getByRole('region', { name: '对白与配乐' });
  await expect(panel.getByText(/Voice Profile v1/).first()).toBeVisible();
  await expect(panel.getByText('Production Sound', { exact: true })).toBeVisible();
  const audio = panel.locator('audio').first();
  await audio.evaluate(async (element) => {
    const player = element as HTMLAudioElement;
    await player.play();
  });
  await expect
    .poll(() => audio.evaluate((element) => (element as HTMLAudioElement).currentTime))
    .toBeGreaterThan(0);
  const state = await (await page.request.get(`/api/projects/${info.project_id}/studio`)).json();
  const final = state.artifacts.find(
    (a: { artifact_id: string; selected: boolean }) => a.artifact_id === 'final_film' && a.selected,
  );
  expect(final.metadata.audio_stems).toHaveLength(3);
  for (const ref of [final, final.metadata.subtitle_artifact, ...final.metadata.audio_stems]) {
    const response = await page.request.get(
      `/api/projects/${info.project_id}/artifacts/${ref.artifact_id}/versions/${ref.version}/download`,
    );
    expect(response.status()).toBe(200);
    expect(
      createHash('sha256')
        .update(await response.body())
        .digest('hex'),
    ).toBe(ref.sha256 || ref.metadata.sha256);
  }
  await page.screenshot({ path: 'test-results/p4d-audio.png', fullPage: true });
  await page.reload();
  const after = await (await page.request.get('/api/p4d/project')).json();
  expect(after.audio_requests).toBe(info.audio_requests);
});
