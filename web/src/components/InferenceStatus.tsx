import type { Snapshot } from '../api/types';
import { t, useLocale } from '../i18n';
import { ApiError } from '../api/client';

export const isConnectionError = (error: unknown) =>
  error instanceof ApiError && [0, 502, 503, 504].includes(error.status);

export const hasInvalidOutput = (snapshot?: Snapshot) =>
  snapshot?.status === 'failed' &&
  snapshot.roles.some((r) => !r.committed && r.failure_code === 'ROLE_OUTPUT_INVALID');

export function InferenceStatus({
  snapshot,
  busy,
  canResume,
  onResume,
  onReplan,
}: {
  snapshot?: Snapshot;
  busy: boolean;
  canResume: boolean;
  onResume: () => void;
  onReplan?: () => void;
}) {
  useLocale();
  if (!snapshot) return null;
  const failedNode = snapshot.graph.nodes.find((n) => n.status === 'failed');
  const invalidRole = snapshot.roles.find(
    (r) => !r.committed && r.failure_code === 'ROLE_OUTPUT_INVALID',
  );
  const uncertain =
    snapshot.failure_code === 'remote_completion_uncertain' ||
    snapshot.roles.some(
      (r) =>
        r.invocation.node_id === failedNode?.node_id &&
        r.failure_code === 'remote_completion_uncertain',
    );
  if (snapshot.status === 'failed')
    return (
      <div className="message error" role="alert">
        {uncertain ? (
          <>
            <strong>
              {t(failedNode?.label || 'Inference')}
              {t(' wait timed out')}
            </strong>
            <span>
              {t(
                'The model request did not return a complete result within the waiting budget. The remote model may still be computing, so the system will not automatically submit it again.',
              )}
            </span>
            <button disabled={busy || !canResume} onClick={onResume}>
              {t('Re-execute ')}
              {t(failedNode?.label || 'Inference')}
            </button>
          </>
        ) : invalidRole ? (
          <>
            <strong>{t('Planning validation needs correction')}</strong>
            <span>
              {invalidRole.invocation.scene_id} —{' '}
              {t('Structured repair budget exhausted. An explicit planning revision is required.')}
            </span>
            <ul>
              {(
                invalidRole.validation_replays.at(-1)?.validation.issues ??
                invalidRole.attempts.at(-1)?.validation.issues ??
                []
              ).map((issue, index) => (
                <li key={index}>
                  <code>{issue.path}</code>: {issue.message}
                </li>
              ))}
            </ul>
            {snapshot.terminal_revision_scene_id ? (
              <>
                <span>
                  {t(
                    'Authorize one scene-local semantic revision, with at most two structured repairs. Previous attempts are retained.',
                  )}
                </span>
                <button disabled={busy || !onReplan} onClick={onReplan}>
                  {t('Correct planning / Replan')}
                </button>
              </>
            ) : (
              <span>
                {t(
                  'No further automatic revision is available. Inspect validation issues before changing the contract.',
                )}
              </span>
            )}
          </>
        ) : (
          <span>{t(`Production failed: ${snapshot.failure_code || 'See node details'}`)}</span>
        )}
      </div>
    );
  const active = snapshot.roles.some(
    (r) => !r.committed && r.inference_records.at(-1)?.provider_outcome === 'submitted',
  );
  if (snapshot.status === 'running' && active)
    return (
      <div className="message" role="status">
        {t('Waiting for the model to finish a longer structured plan…')}
      </div>
    );
  return null;
}
