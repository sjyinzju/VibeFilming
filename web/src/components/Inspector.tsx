import { t, useLocale, localeDate } from '../i18n';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, ArrowDown, Check, FileText } from 'lucide-react';
import { api } from '../api/client';
import type { Snapshot, Artifact, Review } from '../api/types';

export function RawJson({ value }: { value: unknown }) {
  return (
    <pre className="json-view" tabIndex={0}>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
export function MockBadge() {
  return <span className="badge mock">{t('MOCK')}</span>;
}
export function isMock(artifact: Artifact) {
  return (
    artifact.provenance?.tool?.startsWith('mock') ||
    artifact.provenance?.provider_id?.includes('mock') ||
    artifact.uri.endsWith('.placeholder')
  );
}
export function ReviewPanel({
  review,
  onResolve,
  busy,
}: {
  review: Review;
  onResolve: (r: Review, approved: boolean, notes: string) => Promise<void>;
  busy: boolean;
}) {
  const [notes, setNotes] = useState('');
  return (
    <section className="review-card" data-review-id={review.review_id}>
      <div className="eyebrow">
        {t('HUMAN REVIEW · ')}
        {t(review.status || 'pending')}
      </div>
      <h3>{t(review.question)}</h3>
      {review.status === 'pending' ? (
        <>
          <label htmlFor={`review-${review.review_id}`}>{t('Your notes')}</label>
          <textarea
            id={`review-${review.review_id}`}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder={t('What should be preserved or revised?')}
          />
          <div className="review-actions">
            <button
              className="primary"
              disabled={busy}
              onClick={() => onResolve(review, true, notes)}
            >
              <Check size={14} />
              {t(' Approve & resume ')}
            </button>
            <button
              disabled={busy || !notes.trim()}
              onClick={() => onResolve(review, false, notes)}
            >
              {t(' Request revision ')}
            </button>
          </div>
          <p className="help">
            {t(
              ' Revision records your feedback and holds production. Automatic replanning is not available in this phase. ',
            )}
          </p>
        </>
      ) : (
        <>
          <p>{review.resolution_notes || t('No notes supplied.')}</p>
          {review.status === 'rejected' && (
            <p>{t('Production is held for a revision. This review cannot be resolved again.')}</p>
          )}
        </>
      )}
    </section>
  );
}
function ArtifactCard({ artifact }: { artifact: Artifact }) {
  return (
    <details className="artifact-card">
      <summary>
        <FileText size={14} />
        <span>
          {artifact.artifact_type}
          {t(' · v')}
          {artifact.version}
        </span>
        {isMock(artifact) && <MockBadge />}
        {artifact.selected && <span className="badge">{t('Selected')}</span>}
      </summary>
      <p className="mono">{artifact.artifact_id}</p>
      <p>{artifact.provenance?.tool || artifact.provenance?.provider_id || 'Core artifact'}</p>
      {isMock(artifact) && (
        <p className="help">{t('Placeholder artifact. No playable media has been generated.')}</p>
      )}
      <RawJson value={artifact} />
    </details>
  );
}
export function Inspector({
  snapshot,
  selected,
  tab,
  setTab,
  close,
  onResolve,
  busy,
}: {
  snapshot?: Snapshot;
  selected: string | null;
  tab: string;
  setTab: (s: string) => void;
  close: () => void;
  onResolve: (r: Review, approved: boolean, notes: string) => Promise<void>;
  busy: boolean;
}) {
  useLocale();
  const [raw, setRaw] = useState(false);
  const providers = useQuery({
    queryKey: ['providers'],
    queryFn: api.providers,
    enabled: tab === 'Models',
    staleTime: 30000,
    retry: false,
  });
  const node = snapshot?.graph.nodes?.find((n) => n.node_id === selected);
  const scene = snapshot?.project.scenes?.find((s) => `scene:${s.scene_id}` === selected);
  const shot = snapshot?.project.shots?.find((s) => `shot:${s.shot_id}` === selected);
  const roles =
    snapshot?.roles.filter((r) =>
      node
        ? r.invocation.node_id === node.node_id
        : r.invocation.scene_id === (scene?.scene_id || shot?.scene_id),
    ) || [];
  const jobs =
    snapshot?.jobs.filter((j) =>
      node ? j.node_id === node.node_id : shot ? j.shot_id === shot.shot_id : false,
    ) || [];
  const artifacts =
    snapshot?.artifacts.filter(
      (a) =>
        node?.output_refs?.includes(a.artifact_id) ||
        jobs.some((j) => j.job_id === a.source_job_id),
    ) || [];
  const reviews = snapshot?.reviews.filter((r) => r.node_id === node?.node_id) || [];
  return (
    <aside className="inspector">
      <div className="inspector-heading">
        <span className="eyebrow">{t('03 / INSPECTOR')}</span>
        <button className="icon-button" onClick={close} aria-label={t('Close inspector')}>
          <X size={18} />
        </button>
      </div>
      <div className="inspector-tabs" role="tablist" aria-label={t('Inspector tabs')}>
        {['Node', 'Models', 'Trace', 'Artifacts', 'Architecture'].map((name) => (
          <button key={name} role="tab" aria-selected={tab === name} onClick={() => setTab(name)}>
            {t(name)}
          </button>
        ))}
      </div>
      <div className="inspector-content" role="tabpanel" aria-label={t(tab)}>
        {tab === 'Node' && (
          <>
            {node || scene || shot ? (
              <>
                <h2>{node ? t(node.label) : scene?.title || shot?.shot_id}</h2>
                <div className="segmented">
                  <button aria-pressed={!raw} onClick={() => setRaw(false)}>
                    {t(' Summary ')}
                  </button>
                  <button aria-pressed={raw} onClick={() => setRaw(true)}>
                    {t(' Raw JSON ')}
                  </button>
                </div>
                {raw ? (
                  <RawJson value={{ node, scene, shot, roles, jobs, artifacts, reviews }} />
                ) : (
                  <>
                    {node && (
                      <dl>
                        <dt>{t('Role')}</dt>
                        <dd>{t(node.role)}</dd>
                        <dt>{t('Type')}</dt>
                        <dd>{t(node.node_type)}</dd>
                        <dt>{t('Status')}</dt>
                        <dd>{t(node.status)}</dd>
                        <dt>{t('Progress')}</dt>
                        <dd>{Math.round((node.progress || 0) * 100)}%</dd>
                        <dt>{t('Started')}</dt>
                        <dd>{node.started_at ? localeDate(node.started_at) : t('Not started')}</dd>
                        <dt>{t('Completed')}</dt>
                        <dd>{node.completed_at ? localeDate(node.completed_at) : '—'}</dd>
                      </dl>
                    )}
                    {scene && <p>{scene.purpose}</p>}
                    {shot && (
                      <>
                        <p>{shot.narrative.purpose}</p>
                        <p>
                          {shot.camera.shot_size} · {shot.duration_seconds}
                          {t('s ')}
                        </p>
                        <details>
                          <summary>{t('Canonical continuity states')}</summary>
                          <RawJson
                            value={{ before: shot.state_before, after: shot.expected_state_after }}
                          />
                        </details>
                      </>
                    )}
                    {reviews.map((r) => (
                      <ReviewPanel key={r.review_id} review={r} onResolve={onResolve} busy={busy} />
                    ))}
                    {node && (
                      <details>
                        <summary>
                          {t('Dependencies · ')}
                          {(node.dependencies || []).length}
                        </summary>
                        <RawJson
                          value={{
                            dependencies: node.dependencies,
                            input_refs: node.input_refs,
                            output_refs: node.output_refs,
                          }}
                        />
                      </details>
                    )}
                    {roles.map((role) => (
                      <section className="role-detail" key={role.invocation.invocation_id}>
                        <h3>{t(role.invocation.role_id.replaceAll('_', ' '))}</h3>
                        <p className="mono">
                          {role.target_schema} · {t(role.committed ? 'Committed' : 'Pending')}
                        </p>
                        {role.failure_code && <p role="alert">{role.failure_code}</p>}
                        <details>
                          <summary>{t('Typed inputs')}</summary>
                          <RawJson value={role.context} />
                        </details>
                        <details>
                          <summary>{t('Output')}</summary>
                          <RawJson value={role.output} />
                        </details>
                        <details open={!!role.failure_code}>
                          <summary>
                            {t(' Validation & repair · ')}
                            {role.attempts?.length || 0}
                            {t(' attempts ')}
                          </summary>
                          <RawJson
                            value={{
                              attempts: role.attempts,
                              revalidation: role.validation_replays,
                            }}
                          />
                        </details>
                      </section>
                    ))}
                    {jobs.map((job) => (
                      <details key={job.job_id}>
                        <summary>
                          {t(' Job · ')}
                          {job.task} · {t(job.status)}
                        </summary>
                        {job.failure_reason && <p role="alert">{job.failure_reason}</p>}
                        <RawJson value={job} />
                      </details>
                    ))}
                    {artifacts.map((a) => (
                      <ArtifactCard key={`${a.artifact_id}:${a.version}`} artifact={a} />
                    ))}
                    {node?.node_type === 'repair' && <RawJson value={snapshot?.repairs} />}
                    {node?.node_type === 'quality' && <RawJson value={snapshot?.evaluations} />}
                  </>
                )}
              </>
            ) : (
              <div className="empty-inspector">
                <p>{t('Select a node to explore its inputs, outputs and production history.')}</p>
                {snapshot?.reviews
                  .filter((r) => r.status === 'pending')
                  .map((r) => (
                    <ReviewPanel key={r.review_id} review={r} onResolve={onResolve} busy={busy} />
                  ))}
              </div>
            )}
          </>
        )}
        {tab === 'Models' && (
          <>
            <div className="eyebrow">{t('REASONING')}</div>
            <h2>
              {snapshot?.roles.find((r) => r.served_model)?.served_model ||
                snapshot?.reasoning_provider ||
                'Configured provider'}
            </h2>
            <p className="help">
              {t(
                snapshot?.reasoning_provider?.includes('fake')
                  ? 'Deterministic test reasoning'
                  : 'External reasoning through the backend provider layer',
              )}
            </p>
            {providers.isError && <p role="alert">{t('Provider health is unavailable.')}</p>}
            {providers.data?.map((p) => (
              <p key={p.capability.provider_id}>
                {p.capability.provider_id} · {t(p.healthy ? 'Available' : 'Unavailable')}
              </p>
            ))}
            <ul className="role-list">
              {[
                'Creative Producer',
                'Story Architect',
                'Screenwriter',
                'Visual Director',
                'Director',
                'Cinematographer',
              ].map((r) => (
                <li key={r}>{t(r)}</li>
              ))}
            </ul>
            <div className="eyebrow">{t('MEDIA RUNTIME')}</div>
            {['Frame', 'Image', 'Video', 'Vision / Critic', 'Audio', 'Post / Final'].map((m) => (
              <div className="model-row" key={m}>
                {t(m)}
                <MockBadge />
              </div>
            ))}
            <p className="help">
              {t(
                ' Real media providers can be added behind the existing provider contracts in P3. ',
              )}
            </p>
          </>
        )}
        {tab === 'Trace' && (
          <>
            <h2>{t('Production journal')}</h2>
            <p className="help">
              {t(' Latest 200 durable events. Structured outcomes only; no chain-of-thought. ')}
            </p>
            <ol className="timeline">
              {[...(snapshot?.events || [])].reverse().map((e) => (
                <li key={e.event_id}>
                  <time>{localeDate(e.timestamp, true)}</time>
                  <strong>{t(e.event_type.replaceAll('_', ' '))}</strong>
                  <small>{e.node_id || e.job_id || 'Project'}</small>
                  <details>
                    <summary>{t('Event details')}</summary>
                    <RawJson value={e} />
                  </details>
                </li>
              ))}
            </ol>
          </>
        )}
        {tab === 'Artifacts' && (
          <>
            <h2>{t('Production artifacts')}</h2>
            <p className="help">{t('Immutable versions, selection and provenance.')}</p>
            {snapshot?.artifacts.length ? (
              snapshot.artifacts.map((a) => (
                <ArtifactCard key={`${a.artifact_id}:${a.version}`} artifact={a} />
              ))
            ) : (
              <p>{t('Artifacts will appear as production advances.')}</p>
            )}
          </>
        )}
        {tab === 'Architecture' && (
          <>
            <h2>{t('From story to screen.')}</h2>
            <p className="help">{t('One studio, a coordinated production system.')}</p>
            <div className="architecture">
              {[
                'Web Studio',
                'FastAPI',
                'Movie Agent Core',
                'Role Runtime',
                'Provider Layer',
                'DGX Spark / Future Media Runtime',
              ].map((s, i) => (
                <div key={s}>
                  {i > 0 && <ArrowDown size={16} />}
                  <span>{t(s)}</span>
                </div>
              ))}
            </div>
            <details>
              <summary>{t('Full architecture')}</summary>
              <p>
                {t(
                  ' Core owns Cinematic IR, continuity, the production DAG and finite repair budgets. Role outputs pass schema, semantic and continuity validation before commit and checkpoint. ',
                )}
              </p>
              <p>
                {t(
                  ' The browser sends commands to FastAPI and projects snapshots plus durable SSE. Layout and drafts stay local. Providers own model access; media remains Mock. ',
                )}
              </p>
            </details>
          </>
        )}
      </div>
    </aside>
  );
}
