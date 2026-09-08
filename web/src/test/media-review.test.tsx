import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { MediaReviewPanel } from '../components/MediaReviewPanel';
import { applyEvent } from '../state/workflow';
import { projectCanvas } from '../state/canvas';
import { setLanguage } from '../i18n';
import type { Snapshot, Schema, Review, Event } from '../api/types';
import data from './snapshot.json';

function fixture() {
  setLanguage('en');
  const snapshot = structuredClone(data) as unknown as Snapshot;
  const inspection = {
    result_id: 'vlm-result',
    request_id: 'req',
    project_id: snapshot.project.project_id,
    shot_id: 'shot_001',
    target_artifact_id: 'video_shot_001',
    target_artifact_version: 1,
    target_sha256: 'a'.repeat(64),
    decision: 'human_review',
    proposed_decision: 'repair',
    provider_id: 'qwen3_vl',
    provider_metadata: { model: 'movie-agent-vision' },
    scores: [{ profile: 'camera_motion', score: 0.6 }],
    summary: 'Camera stays static.',
    evidence: ['A person raises a cup.'],
    issues: [
      {
        issue_id: 'camera-issue',
        severity: 'major',
        issue_type: 'camera_motion_mismatch',
        message: 'Movement is too slow.',
        evidence: ['Background perspective stays unchanged.'],
        time_ranges: [{ start_seconds: 4.2, end_seconds: 7.1 }],
        frame_references: [],
        suggested_action: 'change_camera_control',
      },
    ],
  } as unknown as Schema['VisionInspectionResult'];
  snapshot.status = 'waiting_human';
  snapshot.artifacts = snapshot.artifacts.filter(
    (a) => a.artifact_id !== 'video_shot_001' || a.version === 1,
  );
  snapshot.media_provider_bindings.vision = 'qwen3_vl';
  snapshot.media_inspections = [inspection];
  snapshot.media_repairs = [
    {
      inspection_result_id: 'vlm-result',
      actions: [
        { action_id: 'a', action_type: 'change_camera_control', rationale: 'Follow the subject' },
      ],
      unsupported_actions: [],
    },
  ] as unknown as Snapshot['media_repairs'];
  const review = {
    review_id: 'review',
    status: 'pending',
    inspection_result_id: 'vlm-result',
    gate_type: 'agent_escalation',
    node_id: 'repair_accept',
  } as Review;
  return { snapshot, inspection, review };
}

it('projects QC for the latest video version while retaining older failed evidence', () => {
  const { snapshot } = fixture();
  snapshot.project.scenes = [
    { scene_id: 'scene', title: 'Scene' },
  ] as Snapshot['project']['scenes'];
  snapshot.project.shots = [
    {
      shot_id: 'shot_001',
      scene_id: 'scene',
      duration_seconds: 9,
      camera: { shot_size: 'wide', motion: { motion_type: 'static' } },
    },
  ] as Snapshot['project']['shots'];
  const video = {
    artifact_id: 'video_shot_001',
    artifact_type: 'video',
    version: 1,
    provenance: { shot_id: 'shot_001' },
  } as Snapshot['artifacts'][number];
  snapshot.artifacts = [video, { ...video, version: 2 }];
  snapshot.evaluations = [
    {
      layer: 'visual_semantic',
      target_shot_id: 'shot_001',
      target_artifact_id: video.artifact_id,
      target_artifact_version: 1,
      passed: false,
    },
    {
      layer: 'visual_semantic',
      target_shot_id: 'shot_001',
      target_artifact_id: video.artifact_id,
      target_artifact_version: 2,
      passed: true,
    },
  ] as Snapshot['evaluations'];
  const qc = () =>
    projectCanvas(snapshot).nodes.find((n) => n.id === 'shot:shot_001')?.data.media?.qc;
  expect(qc()).toBe('✓');
  snapshot.human_overrides = [
    { target_artifact_id: video.artifact_id, target_artifact_version: 2 },
  ];
  expect(qc()).toBe('Human accepted');
  expect(snapshot.evaluations).toHaveLength(2);
});

it('renders real provider, scores, evidence, times, four actions and no mock badge', () => {
  const f = fixture();
  render(<MediaReviewPanel {...f} onResolve={vi.fn()} />);
  expect(screen.getByText('Qwen3-VL')).toBeVisible();
  expect(screen.getByText('movie-agent-vision')).toBeVisible();
  expect(screen.getByText('60%')).toBeVisible();
  expect(screen.getByText('Background perspective stays unchanged.')).toBeVisible();
  expect(screen.getByRole('button', { name: '4.2–7.1s' })).toBeVisible();
  for (const name of [
    'Keep current version',
    'Apply AI repair',
    'Regenerate video',
    'Suggest changes',
  ])
    expect(screen.getByRole('button', { name })).toBeVisible();
  expect(screen.getByText('WAITING_HUMAN')).toBeVisible();
  expect(screen.queryByText('MOCK')).toBeNull();
});

it('submits raw custom feedback, preserve/change fields and dismissed issue IDs', async () => {
  const resolve = vi.fn().mockResolvedValue(undefined);
  render(<MediaReviewPanel {...fixture()} onResolve={resolve} />);
  fireEvent.click(screen.getByRole('button', { name: 'Suggest changes' }));
  fireEvent.change(screen.getByLabelText('Your feedback'), {
    target: { value: '  Preserve this pause.\n跟拍快一些。  ' },
  });
  fireEvent.change(screen.getByLabelText('Preserve'), { target: { value: 'Face and wardrobe' } });
  fireEvent.change(screen.getByLabelText('Change requests'), {
    target: { value: 'Faster tracking' },
  });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Submit media feedback' }));
  await waitFor(() => expect(resolve).toHaveBeenCalled());
  expect(resolve.mock.calls[0][3]).toMatchObject({
    disposition: 'custom_repair',
    target_artifact_version: 1,
    feedback: '  Preserve this pause.\n跟拍快一些。  ',
    dismissed_issue_ids: ['camera-issue'],
    accepted_issue_ids: [],
    preserve_requirements: ['Face and wardrobe'],
    change_requests: ['Faster tracking'],
  });
});

it('PASS reviews remain visible and allow proactive feedback before Final Gate', () => {
  const f = fixture();
  f.inspection.decision = 'pass';
  f.inspection.issues = [];
  render(<MediaReviewPanel inspection={f.inspection} snapshot={f.snapshot} />);
  expect(screen.getByText('PASS')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Suggest changes' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Keep current version' })).toBeNull();
});

it('SSE projects WAITING_HUMAN and the existing critic node displays the real provider', () => {
  const { snapshot } = fixture();
  const event = {
    event_id: 'live',
    project_id: snapshot.project.project_id,
    event_type: 'human_review_requested',
    node_id: 'visual_semantic_critic',
    timestamp: new Date().toISOString(),
    payload: {},
  } as Event;
  const next = applyEvent(snapshot, event);
  expect(next.graph.nodes.find((n) => n.node_id === 'visual_semantic_critic')?.status).toBe(
    'waiting_human',
  );
  const node = projectCanvas(next).nodes.find((n) => n.id === 'visual_semantic_critic');
  expect(node?.data.subtitle).toBe('Qwen3-VL · movie-agent-vision');
  expect(node?.data.executionGraph).toBeUndefined();
});
