import { render, screen, fireEvent } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ReviewSubjectRenderer } from '../components/ReviewSubjectRenderer';
import type { Schema } from '../api/types';

const output: Schema['StoryBible'] = {
  schema_version: '1.0.0',
  story_bible_id: 'bible-1',
  synopsis: '真实故事，不重新总结',
  themes: ['记忆'],
  acts: ['发现信号', '决定回复'],
  character_arcs: { 林: '逃避到承担' },
  world_facts: ['信号只能接收一次'],
  immutable_facts: ['结局保持开放'],
};
const subject: Schema['ReviewSubject'] = {
  schema_version: '1.0.0',
  review_id: 'review-1',
  gate_type: 'story_approval',
  review_node_id: 'opaque-gate',
  subject_type: 'StoryBible',
  subject_title: 'Story proposal',
  unavailable_reason: null,
  sources: [
    {
      schema_version: '1.0.0',
      source_node_id: 'opaque-source',
      artifacts: [],
      evaluations: [],
      content: { schema_version: '1.0.0', kind: 'StoryBible', output },
      role_result: {
        schema_version: '1.0.0',
        invocation: {
          schema_version: '1.0.0',
          invocation_id: 'inv-1',
          role_id: 'story_architect',
          project_id: 'p',
          node_id: 'opaque-source',
          scene_id: null,
          contract_revision: null,
          request_contract_revision: null,
          semantic_revision: null,
        },
        provider_id: 'recorded-provider',
        served_model: null,
        context: {
          schema_version: '1.0.0',
          payload: {},
          source_ids: [],
          context_hash: 'hash',
          context_version: '1',
        },
        target_schema: 'StoryBible',
        target_schema_version: '1.0.0',
        output,
        attempts: [],
        committed: true,
        failure_code: null,
        pending_output: null,
        terminal_repair_base: null,
        inference_records: [],
        validation_replays: [],
      },
    },
  ],
};

it('renders every StoryBible field deterministically and raw JSON is the original output', () => {
  render(<ReviewSubjectRenderer subject={subject} />);
  for (const text of [
    '真实故事，不重新总结',
    '记忆',
    '发现信号',
    '决定回复',
    '林: 逃避到承担',
    '信号只能接收一次',
    '结局保持开放',
  ]) {
    expect(screen.getByText(text)).toBeVisible();
  }
  fireEvent.click(screen.getByRole('button', { name: 'Raw JSON' }));
  expect(JSON.parse(screen.getByTestId('review-output').textContent!)).toEqual(output);
  fireEvent.click(screen.getByRole('button', { name: 'Summary' }));
  expect(screen.getByText(output.synopsis)).toBeVisible();
});

it('navigates using the explicit source identity', () => {
  const onSource = vi.fn();
  render(<ReviewSubjectRenderer subject={subject} onSource={onSource} />);
  fireEvent.click(screen.getByRole('button', { name: 'View source node' }));
  expect(onSource).toHaveBeenCalledWith('opaque-source');
});

it('missing subjects report unavailable, never substitute the current project', () => {
  render(<ReviewSubjectRenderer />);
  expect(screen.getByRole('status')).toHaveTextContent('Review subject unavailable');
  expect(screen.queryByRole('button', { name: 'Raw JSON' })).not.toBeInTheDocument();
});
