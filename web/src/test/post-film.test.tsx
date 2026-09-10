import { render, screen, fireEvent } from '@testing-library/react';
import { expect, it } from 'vitest';
import { PostFilmPanel, PostProgress } from '../components/PostFilmPanel';
import type { Artifact, Schema } from '../api/types';
const artifact = {
  artifact_id: 'final_film',
  artifact_type: 'final_film',
  version: 2,
  provenance: { project_id: 'project_123' },
  metadata: {
    mock: false,
    width: 1920,
    height: 1080,
    fps: 24,
    duration_seconds: 30,
    size_bytes: 123456,
    video_codec: 'h264',
    audio_codec: 'aac',
    sample_rate: 48000,
    sha256: 'abc',
    manifest_artifact_id: 'manifest',
    subtitle_artifact: { artifact_id: 'subtitles', version: 1 },
    render_plan: {
      segments: [
        {
          shot_id: 'shot1',
          start: 0,
          end: 30,
          transition: 'cut',
          generated_silence: true,
          video: { artifact_id: 'video', version: 3 },
        },
      ],
    },
  },
} as unknown as Artifact;
it('shows exact final metadata, playback and safe download links including subtitle and manifest', () => {
  const { container } = render(<PostFilmPanel artifact={artifact} />);
  expect(screen.getByText('1920 × 1080')).toBeVisible();
  expect(screen.getByText('REAL · mock=false')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Download MP4' })).toHaveAttribute(
    'href',
    '/api/projects/project_123/artifacts/final_film/versions/2/download',
  );
  expect(screen.getByRole('link', { name: 'Download subtitles' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Download Render Manifest' })).toBeVisible();
  fireEvent.error(container.querySelector('video')!);
  expect(screen.getByRole('alert')).toBeVisible();
});
it('renders rough cut and determinate pass progress or activity without a made-up percentage', () => {
  render(<PostFilmPanel artifact={{ ...artifact, artifact_type: 'video' }} />);
  expect(screen.getByTestId('rough-cut')).toBeVisible();
  const job = {
    activity: 'FFmpeg · conform 1/2 · frame 10',
    progress: 0.5,
    progress_is_determinate: true,
    status: 'running',
  } as Schema['GenerationJob'];
  const { rerender } = render(<PostProgress job={job} />);
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0.5');
  rerender(
    <PostProgress
      job={{ ...job, progress_is_determinate: false, status: 'failed', failure_reason: 'timeout' }}
    />,
  );
  expect(screen.queryByRole('progressbar')).toBeNull();
  expect(screen.getByRole('alert')).toHaveTextContent('timeout');
});

it('labels candidate exports without implying final human approval', () => {
  render(<PostFilmPanel artifact={{ ...artifact, artifact_id: 'any_candidate',
    metadata: { ...artifact.metadata, export_intent: 'candidate', human_aesthetically_approved: false } }} />);
  expect(screen.getByTestId('candidate-film')).toBeVisible();
  expect(screen.getByText('Human aesthetic review pending')).toBeVisible();
  expect(screen.queryByTestId('final-film')).toBeNull();
  expect(screen.getByRole('link', { name: 'Download MP4' })).toHaveAttribute(
    'href', '/api/projects/project_123/artifacts/any_candidate/versions/2/download');
});
