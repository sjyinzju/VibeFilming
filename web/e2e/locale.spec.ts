import { test, expect } from '@playwright/test';

for (const scenario of [
  { ui: 'en', explicit: false },
  { ui: 'zh', explicit: true },
]) {
  test(`create request uses English: UI=${scenario.ui}, explicit=${scenario.explicit}`, async ({
    page,
  }) => {
    await page.goto('/');
    await page.getByLabel('界面语言 / Interface language').selectOption(scenario.ui);
    await page.locator('#story_description').fill('A person answers a signal.');
    if (scenario.explicit) await page.locator('#output_language').selectOption('en');
    else await expect(page.locator('#output_language')).toHaveValue('follow-ui');
    const creation = page.waitForRequest(
      (request) => request.method() === 'POST' && request.url().endsWith('/projects'),
    );
    await page.locator('.start-film').click();
    expect((await creation).postDataJSON().output_language).toBe('en');
    await expect(page.locator('#output_language')).toHaveValue('en');
    await expect(page.locator('#output_language')).toBeDisabled();
  });
}

test('defaults to Chinese and remembers English across refresh without losing the draft', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await expect(page.getByRole('button', { name: '开始制作', exact: true })).toBeVisible();
  await page.getByLabel('你的故事').fill('保留这个故事 — keep my story.');
  await page.getByRole('button', { name: '高级设置', exact: true }).click();
  await expect(page.getByLabel('影片输出语言')).toHaveValue('follow-ui');
  await page.getByLabel('界面语言 / Interface language').selectOption('en');
  await expect(page.getByRole('button', { name: 'Start Film', exact: true })).toBeVisible();
  await expect(page.getByLabel('Your story')).toHaveValue('保留这个故事 — keep my story.');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.getByLabel('Your story')).toHaveValue('保留这个故事 — keep my story.');
  await page.getByLabel('界面语言 / Interface language').selectOption('zh');
  await page.getByRole('button', { name: '开始制作', exact: true }).click();
  await expect(page.getByRole('button', { name: '批准并继续' })).toBeVisible();
  const pid = new URL(page.url()).searchParams.get('project')!;
  const submitted = await (await page.request.get(`/api/projects/${pid}/studio`)).json();
  expect(submitted.project.brief.output_language).toBe('zh-CN');
  await page.getByLabel('审核意见').fill('保留我的意见');
  await page.getByLabel('界面语言 / Interface language').selectOption('en');
  await expect(page.getByLabel('Your notes')).toHaveValue('保留我的意见');
  const switched = await (await page.request.get(`/api/projects/${pid}/studio`)).json();
  expect(switched.project.brief.output_language).toBe('zh-CN');
  expect(switched.roles).toEqual(submitted.roles);
  expect(switched.project.story_bible).toEqual(submitted.project.story_bible);
  await expect(page.getByRole('heading', { name: 'Story Approval', exact: true })).toBeVisible();
  await page.getByLabel('界面语言 / Interface language').selectOption('zh');
  await expect(page.getByRole('heading', { name: '故事审核', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/studio-zh.png', fullPage: true });
});
