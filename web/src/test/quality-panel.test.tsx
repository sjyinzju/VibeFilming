import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { QualityPanel } from '../components/QualityPanel';
import fixture from './snapshot.json';
import type { Snapshot } from '../api/types';

test('quality dashboard keeps missing evidence and human review explicit', () => {
  localStorage.setItem('movie-agent:locale', 'en');
  render(<QualityPanel snapshot={fixture as Snapshot} />);
  expect(screen.getByText(/Quality evidence pending|质量证据待生成/)).toBeInTheDocument();
  expect(
    screen.getByText(/Human aesthetic and listening review pending|人工审美与试听待确认/),
  ).toBeInTheDocument();
  expect(screen.getByRole('combobox')).toBeInTheDocument();
});

test('quality denominator includes planned shots blocked before video generation', () => {
  const snapshot = structuredClone(fixture) as Snapshot;
  snapshot.project.shots = ['blocked-1', 'blocked-2'].map(
    (shot_id) => ({ shot_id }) as Snapshot['project']['shots'][number],
  );
  snapshot.film_quality = {
    schema_version: '1.0.0', project_id: snapshot.project.project_id,
    profile: 'showcase', shots: [], scenes: [], accepted_seconds: 0, accepted_shots: 0,
    minimum_runtime_met: false, minimum_shots_met: false,
    human_aesthetically_approved: false, review_proxy_artifact_id: null,
    cinematic_evaluation_id: null,
  };
  render(<QualityPanel snapshot={snapshot} />);
  expect(screen.getByText(/0\/2/)).toBeInTheDocument();
});
