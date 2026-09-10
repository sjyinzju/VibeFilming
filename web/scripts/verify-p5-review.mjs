// Read-only visual and download verification of the actual isolated P5 project.
import { chromium } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
const root = new URL('../../workspace/p5-hero-film/', import.meta.url);
const pid = (await readFile(new URL('project-id.txt', root), 'utf8')).trim();
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://127.0.0.1:8099/morning-review.html');
  await page.getByText('候选片：未生成', { exact: false }).waitFor();
  assert.equal(await page.locator('audio').count(), 6);
  assert.equal(await page.locator('figure img').count(), 10);
  for (const img of await page.locator('figure img').all()) {
    await img.scrollIntoViewIfNeeded();
    await img.evaluate(el => el.decode());
    assert(await img.evaluate(el => el.naturalWidth > 0));
  }
  const durations = await page.locator('audio').evaluateAll(async elements => Promise.all(elements.map(el => new Promise((resolve, reject) => {
    el.onloadedmetadata = () => resolve(el.duration);
    el.onerror = () => reject(new Error('Audio metadata failed: ' + el.currentSrc));
    el.preload = 'metadata'; el.load();
  }))));
  assert(durations.every(d => Number.isFinite(d) && d > 0));
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: new URL('morning-review.png', root).pathname.replace(/^\/([A-Z]:)/, '$1'), fullPage: false });
  await page.goto(`http://127.0.0.1:5189/?project=${pid}`);
  await page.getByRole('tab', { name: /^(Quality|质量)$/ }).click();
  await page.getByRole('heading', { name: /Quality dashboard|质量总览/ }).waitFor();
  await page.getByText('0.0 s · 0/12 已通过 · showcase', { exact: true }).waitFor();
  await page.screenshot({ path: new URL('studio-quality.png', root).pathname.replace(/^\/([A-Z]:)/, '$1'), fullPage: false });
  const snapshot = await (await page.request.get(`http://127.0.0.1:8089/projects/${pid}/studio`)).json();
  assert.equal(snapshot.project.shots.length, 12);
  assert.equal(snapshot.film_quality.accepted_seconds, 0);
  const media = snapshot.artifacts.filter(a => ['frame', 'audio'].includes(a.artifact_type) && !a.metadata.mock);
  const downloads = [];
  for (const a of media) {
    const response = await page.request.get(`http://127.0.0.1:8089/projects/${pid}/artifacts/${a.artifact_id}/versions/${a.version}/download`);
    assert.equal(response.status(), 200);
    const digest = createHash('sha256').update(await response.body()).digest('hex');
    assert.equal(digest, a.metadata.sha256);
    assert.equal(response.headers()['x-artifact-sha256'], digest);
    downloads.push({ artifact_id: a.artifact_id, version: a.version, sha256: digest, status: response.status(), content_type: response.headers()['content-type'] });
  }
  assert.equal(media.length, 18);
  const audio = media.find(a => a.artifact_type === 'audio');
  const range = await page.request.get(`http://127.0.0.1:8089/artifacts/${audio.artifact_id}/content?project_id=${pid}&version=${audio.version}`, { headers: { Range: 'bytes=0-1023' } });
  assert.equal(range.status(), 206);
  assert.equal((await range.body()).length, 1024);
  assert.deepEqual(errors, []);
  const result = { project_id: pid, page_errors: errors, reference_images_decoded: 10, audio_metadata_durations: durations,
    downloads, audio_byte_range: { status: 206, bytes: 1024 }, no_candidate: true, human_listening: 'pending; metadata decoding does not establish intelligibility or aesthetics' };
  await writeFile(new URL('review-verification.json', root), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ images: 10, audio: durations, exact_downloads: downloads.length, page_errors: errors }));
} finally { await browser.close(); }
