import { test, expect } from '@playwright/test';

test('defaults to Chinese and remembers English across refresh without losing the draft', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await expect(page.getByRole('button', { name: '开始制作', exact: true })).toBeVisible();
  await page.getByLabel('你的故事').fill('保留这个故事 — keep my story.');
  await page.getByRole('button', { name: '高级设置', exact: true }).click();
  await expect(page.getByLabel('影片输出语言')).toHaveValue('en');
  await page.getByLabel('界面语言 / Interface language').selectOption('en');
  await expect(page.getByRole('button', { name: 'Start Film', exact: true })).toBeVisible();
  await expect(page.getByLabel('Your story')).toHaveValue('保留这个故事 — keep my story.');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.getByLabel('Your story')).toHaveValue('保留这个故事 — keep my story.');
  await page.getByLabel('界面语言 / Interface language').selectOption('zh');
  await page.getByRole('button', { name: '开始制作', exact: true }).click();
  await expect(page.getByRole('button', { name: '批准并继续' })).toBeVisible();
  await page.getByLabel('审核意见').fill('保留我的意见');
  await page.getByLabel('界面语言 / Interface language').selectOption('en');
  await expect(page.getByLabel('Your notes')).toHaveValue('保留我的意见');
  await expect(page.getByRole('heading', { name: 'Story Approval', exact: true })).toBeVisible();
  await page.getByLabel('界面语言 / Interface language').selectOption('zh');
  await expect(page.getByRole('heading', { name: '故事审核', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/studio-zh.png', fullPage: true });
});
