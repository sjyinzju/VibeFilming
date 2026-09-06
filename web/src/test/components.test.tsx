import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import {
  BriefConsole,
  validateBrief,
  toInput,
  fields,
  fieldLabel,
} from '../components/BriefConsole';
import { Inspector, MockBadge, ReviewPanel } from '../components/Inspector';
import snapshotData from './snapshot.json';
import type { Snapshot, Review } from '../api/types';

it('validates the single required story and numeric production boundaries', () => {
  expect(validateBrief({ story_description: '   ' })).toBeTruthy();
  expect(validateBrief({ story_description: 'Signal', target_duration: -1 })).toBeTruthy();
  expect(validateBrief({ story_description: 'Signal', max_shots: 1.5 })).toBeTruthy();
  expect(validateBrief({ story_description: 'Signal' })).toBeNull();
  expect(toInput({ story_description: 'Signal' })).toMatchObject({
    title: 'Untitled film',
    target_duration: 30,
    output_language: 'en',
  });
});
it('advanced brief renders every backend brief field with progressive controls', () => {
  const change = vi.fn();
  render(
    <BriefConsole
      draft={{ story_description: '' }}
      onChange={change}
      locked={false}
      busy={false}
      onStart={() => {}}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
  for (const key of Object.keys(fields)) {
    if (key === 'schema_version') continue;
    const label = fieldLabel(key);
    expect(screen.getAllByText(label, { exact: false }).length).toBeGreaterThan(0);
  }
  fireEvent.click(screen.getByText('Characters', { selector: 'summary' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add character' }));
  expect(change).toHaveBeenCalledWith(expect.objectContaining({ character_descriptions: [''] }));
});
it('brief invokes Start Film and submitted input is read-only', () => {
  const start = vi.fn();
  const { rerender } = render(
    <BriefConsole
      draft={{ story_description: 'Signal' }}
      onChange={() => {}}
      locked={false}
      busy={false}
      onStart={start}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Start Film' }));
  expect(start).toHaveBeenCalledOnce();
  rerender(
    <BriefConsole
      draft={{ story_description: 'Signal' }}
      onChange={() => {}}
      locked
      busy={false}
      onStart={start}
    />,
  );
  expect(screen.getByLabelText('Your story', { exact: false })).toBeDisabled();
});
it('human review approval and revision pass the actual review identity and notes', () => {
  const review: Review = {
    schema_version: '1.0.0',
    review_id: 'review-1',
    project_id: 'project-1',
    node_id: 'story_gate',
    gate_type: 'story_approval',
    status: 'pending',
    question: 'Approve story?',
    context_artifact_ids: [],
    requested_at: new Date().toISOString(),
    resolved_at: null,
    resolution_notes: null,
  };
  const resolve = vi.fn().mockResolvedValue(undefined);
  render(<ReviewPanel review={review} onResolve={resolve} busy={false} />);
  expect(screen.getByRole('button', { name: 'Request revision' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Your notes'), {
    target: { value: 'Keep the ending open.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Request revision' }));
  expect(resolve).toHaveBeenLastCalledWith(review, false, 'Keep the ending open.');
  fireEvent.click(screen.getByRole('button', { name: 'Approve & resume' }));
  expect(resolve).toHaveBeenLastCalledWith(review, true, 'Keep the ending open.');
});
it('inspector shows typed node details and Raw JSON', () => {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <Inspector
        snapshot={snapshotData as Snapshot}
        selected="brief"
        tab="Node"
        setTab={() => {}}
        close={() => {}}
        onResolve={async () => {}}
        busy={false}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByRole('heading', { name: 'Brief' })).toBeVisible();
  expect(screen.getByText('Showrunner', { exact: true })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Raw JSON' }));
  expect(document.querySelector('pre')?.textContent).toContain('"node_id": "brief"');
});
it('Mock badges are explicit textual labels', () => {
  render(<MockBadge />);
  expect(screen.getByText('MOCK')).toBeVisible();
});
