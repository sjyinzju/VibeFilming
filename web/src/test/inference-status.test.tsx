import { render, screen, fireEvent } from '@testing-library/react';
import { it, expect, vi } from 'vitest';
import {
  InferenceStatus,
  isConnectionError,
  hasInvalidOutput,
} from '../components/InferenceStatus';
import { ApiError } from '../api/client';
import type { Snapshot } from '../api/types';
import snapshotData from './snapshot.json';
import { setLanguage } from '../i18n';

it('typed inference timeout offers explicit resume, never reconnect or automatic retry', () => {
  setLanguage('zh');
  const snapshot = structuredClone(snapshotData) as Snapshot;
  snapshot.status = 'failed';
  snapshot.failure_code = 'remote_completion_uncertain';
  snapshot.graph.nodes.find((n) => n.node_id === 'scene_planning')!.status = 'failed';
  const resume = vi.fn();
  render(<InferenceStatus snapshot={snapshot} busy={false} canResume onResume={resume} />);
  expect(screen.getByRole('alert')).toHaveTextContent('场景规划');
  expect(screen.getByRole('alert')).toHaveTextContent('等待超时');
  expect(screen.getByRole('alert')).toHaveTextContent('系统不会自动重复请求');
  expect(screen.queryByText('重新连接')).not.toBeInTheDocument();
  expect(resume).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /重新执行/ }));
  expect(resume).toHaveBeenCalledOnce();
});

it('only connection errors use reconnect; command conflicts do not', () => {
  expect(isConnectionError(new ApiError(0, 'offline'))).toBe(true);
  expect(isConnectionError(new ApiError(503, 'unavailable'))).toBe(true);
  expect(isConnectionError(new ApiError(409, 'review needed'))).toBe(false);
  expect(isConnectionError(new Error('ProviderFailure'))).toBe(false);
});

it('semantic exhaustion displays real issues and requires an explicit local revision', () => {
  setLanguage('zh');
  const snapshot = structuredClone(snapshotData) as Snapshot;
  snapshot.status = 'failed';
  snapshot.failure_code = 'ROLE_OUTPUT_INVALID';
  const role: Snapshot['roles'][number] = {
    schema_version: '1.0.0',
    invocation: {
      schema_version: '1.0.0',
      invocation_id: 'i',
      project_id: snapshot.project.project_id,
      role_id: 'cinematographer',
      node_id: 'shot_planning',
      scene_id: 'scene_3',
      contract_revision: null,
      request_contract_revision: null,
      semantic_revision: null,
    },
    provider_id: 'real',
    served_model: null,
    target_schema: 'ShotPlanDraft',
    target_schema_version: '1.0.0',
    context: {
      schema_version: '1.0.0',
      payload: {},
      source_ids: [],
      context_hash: 'h',
      context_version: '3',
    },
    output: null,
    committed: false,
    failure_code: 'ROLE_OUTPUT_INVALID',
    pending_output: null,
    terminal_repair_base: null,
    inference_records: [],
    validation_replays: [],
    attempts: [
      {
        schema_version: '1.0.0',
        request_id: 'r',
        prompt_hash: 'p',
        latency_seconds: 1,
        token_usage: {},
        timestamp: '2026-09-06T00:00:00Z',
        raw_output: null,
        request_prompt: null,
        validation: { schema_version: '1.0.0', issues: [], unverified_constraints: [] },
      },
    ],
  };
  snapshot.roles = [role];
  role.committed = false;
  role.failure_code = 'ROLE_OUTPUT_INVALID';
  role.validation_replays = [];
  role.attempts[0].validation.issues = [
    {
      schema_version: '1.0.0',
      code: 'semantic_conflict',
      path: 'scene_3-CHAIN',
      message: 'observed safe; required protective',
    },
  ];
  snapshot.terminal_revision_scene_id = 'scene_3';
  const resume = vi.fn(),
    replan = vi.fn();
  const { rerender } = render(
    <InferenceStatus
      snapshot={snapshot}
      busy={false}
      canResume={false}
      onResume={resume}
      onReplan={replan}
    />,
  );
  expect(hasInvalidOutput(snapshot)).toBe(true);
  expect(screen.getByRole('alert')).toHaveTextContent('observed safe; required protective');
  expect(resume).not.toHaveBeenCalled();
  expect(replan).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '修正规划 / 重新规划' }));
  expect(replan).toHaveBeenCalledOnce();
  snapshot.terminal_revision_scene_id = null;
  rerender(
    <InferenceStatus
      snapshot={snapshot}
      busy={false}
      canResume={false}
      onResume={resume}
      onReplan={replan}
    />,
  );
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(screen.queryByText('重新连接')).not.toBeInTheDocument();
});

it('running request remains running instead of displaying a failed timer', () => {
  const snapshot = structuredClone(snapshotData) as Snapshot;
  snapshot.status = 'running';
  snapshot.roles = [
    {
      schema_version: '1.0.0',
      invocation: {
        schema_version: '1.0.0',
        invocation_id: 'i',
        project_id: snapshot.project.project_id,
        role_id: 'director',
        node_id: 'scene_planning',
        scene_id: null,
        contract_revision: null,
        request_contract_revision: null,
        semantic_revision: null,
      },
      provider_id: 'reasoning',
      served_model: null,
      target_schema: 'ScenePlan',
      target_schema_version: '1.0.0',
      context: {
        schema_version: '1.0.0',
        payload: {},
        source_ids: [],
        context_hash: 'h',
        context_version: '1',
      },
      output: null,
      committed: false,
      attempts: [],
      validation_replays: [],
      pending_output: null,
      terminal_repair_base: null,
      failure_code: null,
      inference_records: [
        {
          schema_version: '1.0.0',
          request_id: 'request',
          role_id: 'director',
          scene_id: null,
          context_chars: 100,
          prompt_chars: 100,
          input_token_estimate: 100,
          schema_chars: 100,
          max_output_tokens: 8192,
          read_timeout: 360,
          total_timeout: 360,
          inactivity_timeout: 60,
          streaming: true,
          failure_phase: null,
          started_at: '2020-01-01T00:00:00Z',
          ended_at: null,
          latency_seconds: 0,
          provider_outcome: 'submitted',
          prompt_tokens: null,
          completion_tokens: null,
          finish_reason: null,
          validation_result: 'not_run',
        },
      ],
    },
  ];
  render(
    <InferenceStatus snapshot={snapshot} busy={false} canResume={false} onResume={() => {}} />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('longer structured plan');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
