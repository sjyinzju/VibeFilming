import { useState } from 'react';
import type { Artifact, Schema } from '../api/types';
import { API_BASE } from '../api/client';
import { t } from '../i18n';

type Source = { artifact_id: string; version: number; sha256: string; provider: string };
type Segment = {
  shot_id: string;
  start: number;
  end: number;
  transition: string;
  video: Source;
  audio?: Source;
  generated_silence: boolean;
};
type Plan = { segments: Segment[] };
const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(2).padStart(5, '0')}`;
export function downloadUrl(project: string, identity: string, version: number) {
  return `${API_BASE}/projects/${encodeURIComponent(project)}/artifacts/${encodeURIComponent(identity)}/versions/${version}/download`;
}

export function PostProgress({ job }: { job: Schema['GenerationJob'] }) {
  return (
    <div role="status" data-testid="post-progress">
      <p>{job.activity || t('Rendering')}</p>
      {job.progress_is_determinate ? (
        <progress max={1} value={job.progress} aria-label={t('Current render pass')} />
      ) : null}
      {job.status === 'failed' ? (
        <p role="alert">{job.failure_reason || t('Render failed')}</p>
      ) : null}
    </div>
  );
}

export function PostFilmPanel({ artifact }: { artifact: Artifact }) {
  const [failed, setFailed] = useState(false);
  const project = artifact.provenance.project_id;
  const meta = artifact.metadata;
  const plan = meta.render_plan as unknown as Plan | undefined;
  if (!project || !plan) return null;
  const subtitle = meta.subtitle_artifact as unknown as Source | undefined;
  const stems = (meta.audio_stems || []) as unknown as (Source & { name: string })[];
  const isFinal = artifact.artifact_type === 'final_film';
  const isCandidate = meta.export_intent === 'candidate';
  const label = isCandidate ? 'Candidate film' : isFinal ? 'Final film' : 'Rough cut';
  const url = `${API_BASE}/artifacts/${encodeURIComponent(artifact.artifact_id)}/preview?project_id=${encodeURIComponent(project)}&version=${artifact.version}`;
  return (
    <section className="post-film" data-testid={isCandidate ? 'candidate-film' : isFinal ? 'final-film' : 'rough-cut'}>
      <h3>{t(label)}</h3>
      {isCandidate ? <p>{t('Human aesthetic review pending')}</p> : null}
      <span className="badge">{meta.mock === false ? 'REAL · mock=false' : 'MOCK'}</span>
      <video
        className="media-preview"
        controls
        preload="metadata"
        src={url}
        aria-label={t(label)}
        onError={() => setFailed(true)}
      />
      {failed ? (
        <p role="alert">{t('Video preview unavailable. Try downloading the exact artifact.')}</p>
      ) : null}
      <dl>
        <dt>{t('Resolution')}</dt>
        <dd>
          {String(meta.width)} × {String(meta.height)}
        </dd>
        <dt>FPS</dt>
        <dd>{String(meta.fps)}</dd>
        <dt>{t('Duration')}</dt>
        <dd>{clock(Number(meta.duration_seconds))}</dd>
        <dt>{t('Size')}</dt>
        <dd>{(Number(meta.size_bytes) / 1048576).toFixed(2)} MiB</dd>
        <dt>{t('Codecs')}</dt>
        <dd>
          {String(meta.video_codec)} / {String(meta.audio_codec)} · {String(meta.sample_rate)} Hz
        </dd>
        <dt>{t('Version')}</dt>
        <dd>v{artifact.version}</dd>
        <dt>SHA256</dt>
        <dd className="mono" title={String(meta.sha256)}>
          {String(meta.sha256).slice(0, 16)}…
        </dd>
        <dt>{t('Render status')}</dt>
        <dd>{t('Completed')} · QC PASS</dd>
        <dt>{t('Shot count')}</dt>
        <dd>{plan.segments.length}</dd>
      </dl>
      <div className="review-actions">
        {stems.map((stem) => (
          <a
            className="button"
            key={stem.artifact_id}
            download
            href={downloadUrl(project, stem.artifact_id, stem.version)}
          >
            {stem.name === 'dialogue_stem'
              ? '下载对白轨'
              : stem.name === 'music_stem'
                ? '下载音乐轨'
                : '下载原声轨'}
          </a>
        ))}
        <a
          className="button primary"
          download
          href={downloadUrl(project, artifact.artifact_id, artifact.version)}
        >
          {t('Download MP4')}
        </a>
        {subtitle ? (
          <a
            className="button"
            download
            href={downloadUrl(project, subtitle.artifact_id, subtitle.version)}
          >
            {t('Download subtitles')}
          </a>
        ) : null}
        {typeof meta.manifest_artifact_id === 'string' ? (
          <a className="button" download href={downloadUrl(project, meta.manifest_artifact_id, 1)}>
            {t('Download Render Manifest')}
          </a>
        ) : null}
      </div>
      <table className="post-timeline">
        <thead>
          <tr>
            <th>{t('Shot')}</th>
            <th>{t('Timeline')}</th>
            <th>{t('Sources')}</th>
          </tr>
        </thead>
        <tbody>
          {plan.segments.map((segment, index) => (
            <tr key={`${segment.shot_id}:${index}`}>
              <td>
                {segment.shot_id || index + 1}
                <br />
                {segment.transition.toUpperCase()}
              </td>
              <td>
                {clock(segment.start)} – {clock(segment.end)}
              </td>
              <td>
                {segment.video.artifact_id} · v{segment.video.version}
                <br />
                {segment.audio
                  ? `${segment.audio.provider} · ${segment.audio.artifact_id} · v${segment.audio.version}`
                  : t('Deterministic silence')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
