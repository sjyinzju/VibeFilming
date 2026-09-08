import { useRef, useState } from 'react';
import { api, API_BASE } from '../api/client';
import type { Review, Schema, Snapshot } from '../api/types';
import { t, useLocale } from '../i18n';

type Inspection = Schema['VisionInspectionResult'];
export type MediaResolve = (
  review: Review,
  approved: boolean,
  notes: string,
  directive?: Schema['HumanRepairInput'],
) => Promise<void>;
const profiles: Record<string, string> = {
  video_quality: 'Video quality',
  character_identity: 'Character identity',
  scene_consistency: 'Scene consistency',
  prompt_alignment: 'Prompt alignment',
  action_completion: 'Action completion',
  camera_motion: 'Camera motion',
  continuity: 'Continuity',
  artifact_detection: 'Artifact detection',
  full_shot_review: 'Full shot review',
};

export function MediaReviewPanel({
  inspection,
  snapshot,
  review,
  onResolve,
  onChanged,
  busy = false,
}: {
  inspection: Inspection;
  snapshot: Snapshot;
  review?: Review;
  onResolve?: MediaResolve;
  onChanged?: () => void | Promise<void>;
  busy?: boolean;
}) {
  useLocale();
  const video = useRef<HTMLVideoElement>(null);
  const commandId = useRef(crypto.randomUUID());
  const [custom, setCustom] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [preserve, setPreserve] = useState('');
  const [changes, setChanges] = useState('');
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const preview = snapshot.media_previews.find(
    (p) =>
      p.artifact_id === inspection.target_artifact_id &&
      p.version === inspection.target_artifact_version,
  );
  const plan = snapshot.media_repairs.find((p) => p.inspection_result_id === inspection.result_id);
  const override = snapshot.human_overrides?.find(
    (o) => o.inspection_result_id === inspection.result_id,
  );
  const pending = review?.status === 'pending' && !review.superseded_at;
  const current = !snapshot.artifacts.some(
    (a) =>
      a.artifact_id === inspection.target_artifact_id &&
      a.version > (inspection.target_artifact_version || 0),
  );
  const writable =
    current &&
    Boolean(inspection.target_sha256 && inspection.target_artifact_version) &&
    !['completed', 'cancelled'].includes(snapshot.status);
  const disabled = busy || saving || !writable;
  const submit = async (disposition: Schema['HumanRepairDisposition']) => {
    setSaving(true);
    setError('');
    const command: Schema['HumanRepairInput'] = {
      schema_version: '1.0.0',
      command_id: commandId.current,
      inspection_result_id: inspection.result_id,
      target_artifact_id: inspection.target_artifact_id,
      target_artifact_version: inspection.target_artifact_version!,
      target_sha256: inspection.target_sha256!,
      disposition,
      feedback,
      accepted_issue_ids: inspection.issues
        .filter((i) => !dismissed.includes(i.issue_id))
        .map((i) => i.issue_id),
      dismissed_issue_ids: dismissed,
      preserve_requirements: preserve.split('\n').filter((s) => s.trim()),
      change_requests: changes.split('\n').filter((s) => s.trim()),
    };
    try {
      if (pending && review && onResolve) await onResolve(review, true, feedback, command);
      else {
        await api.mediaFeedback(snapshot.project.project_id, command);
        const next = await api.snapshot(snapshot.project.project_id);
        if (
          ['paused', 'waiting_human', 'failed'].includes(next.status) &&
          next.reviews.every((r) => r.status === 'approved' || r.superseded_at)
        )
          await api.command(snapshot.project.project_id, 'resume');
        await onChanged?.();
      }
      commandId.current = crypto.randomUUID();
      setCustom(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Media feedback failed'));
    } finally {
      setSaving(false);
    }
  };
  return (
    <section
      className="ai-review"
      aria-label={t('AI video review')}
      data-inspection-id={inspection.result_id}
    >
      <div className="eyebrow">
        {t('AI video review')} · {inspection.shot_id || inspection.target_artifact_id} · v
        {inspection.target_artifact_version}
      </div>
      <div className="review-provider">
        {inspection.provider_id === 'qwen3_vl' ? 'Qwen3-VL' : inspection.provider_id}
        {inspection.provider_id.startsWith('mock') ? (
          <span className="badge mock">MOCK</span>
        ) : null}
        {typeof inspection.provider_metadata.model === 'string' ? (
          <small>{inspection.provider_metadata.model}</small>
        ) : null}
      </div>
      <span className={`badge decision-${inspection.decision}`}>
        {inspection.decision.toUpperCase().replaceAll('_', ' ')}
      </span>
      {pending ? <span className="badge">WAITING_HUMAN</span> : null}
      {preview?.playable ? (
        <video
          ref={video}
          className="media-preview"
          controls
          preload="metadata"
          src={`${API_BASE}${preview.preview_url}`}
          aria-label={t('Reviewed video')}
        />
      ) : null}
      <p>{inspection.summary}</p>
      {override ? (
        <p className="human-override">
          {t('Human override: current version accepted; AI findings retained.')}
        </p>
      ) : null}
      <div className="vision-scores" aria-label={t('Review scores')}>
        {inspection.scores.map((score) => (
          <div key={score.profile}>
            <span>{t(profiles[score.profile] || score.profile)}</span>
            <meter min={0} max={1} value={score.score} />{' '}
            <strong>{Math.round(score.score * 100)}%</strong>
          </div>
        ))}
      </div>
      {inspection.evidence.length ? (
        <details>
          <summary>{t('Observed evidence')}</summary>
          {inspection.evidence.map((e, i) => (
            <p key={i}>{e}</p>
          ))}
        </details>
      ) : null}
      <ul className="vision-issues">
        {inspection.issues.map((issue) => (
          <li key={issue.issue_id}>
            <div>
              <span className={`badge severity-${issue.severity}`}>
                {issue.severity.toUpperCase()}
              </span>{' '}
              <code>{issue.issue_type}</code>
            </div>
            <p>{issue.message}</p>
            {issue.evidence.map((e, i) => (
              <blockquote key={i}>{e}</blockquote>
            ))}
            {issue.time_ranges.map((range, i) => (
              <button
                className="timestamp"
                key={i}
                onClick={() => {
                  if (video.current) video.current.currentTime = range.start_seconds;
                }}
              >
                {range.start_seconds.toFixed(1)}–{range.end_seconds.toFixed(1)}s
              </button>
            ))}
            {issue.frame_references.map((frame, i) => (
              <span key={i} className="help">
                {frame.timestamp_seconds != null
                  ? `${frame.timestamp_seconds.toFixed(1)}s`
                  : `frame ${frame.frame_number}`}
              </span>
            ))}
            {issue.suggested_action ? (
              <p className="help">
                {t('Suggested action')}: {issue.suggested_action.replaceAll('_', ' ')}
              </p>
            ) : null}
            {pending || custom ? (
              <label>
                <input
                  type="checkbox"
                  checked={!dismissed.includes(issue.issue_id)}
                  onChange={(e) =>
                    setDismissed((old) =>
                      e.target.checked
                        ? old.filter((id) => id !== issue.issue_id)
                        : [...old, issue.issue_id],
                    )
                  }
                />{' '}
                {t('Accept this issue')} <small>{t('Uncheck to dismiss')}</small>
              </label>
            ) : null}
          </li>
        ))}
      </ul>
      {plan ? (
        <details>
          <summary>{t('AI repair proposal')}</summary>
          {plan.actions.map((a) => (
            <p key={a.action_id}>
              {a.action_type.replaceAll('_', ' ')} — {a.rationale}
            </p>
          ))}
          {plan.requires_human ? <p>{t('Human decision required')}</p> : null}
        </details>
      ) : null}
      {pending ? (
        <div className="review-actions">
          <button disabled={disabled} onClick={() => void submit('keep_current')}>
            {t('Keep current version')}
          </button>
          <button
            disabled={
              disabled || !plan?.actions.length || Boolean(plan.unsupported_actions?.length)
            }
            onClick={() => void submit('apply_ai_repair')}
          >
            {t('Apply AI repair')}
          </button>
          <button disabled={disabled} onClick={() => void submit('regenerate')}>
            {t('Regenerate video')}
          </button>
        </div>
      ) : null}
      {writable ? (
        <button disabled={busy || saving} onClick={() => setCustom((v) => !v)}>
          {t('Suggest changes')}
        </button>
      ) : null}
      {custom ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit('custom_repair');
          }}
        >
          <label>
            {t('Your feedback')}
            <textarea value={feedback} onChange={(e) => setFeedback(e.target.value)} />
          </label>
          <label>
            {t('Preserve')}
            <textarea value={preserve} onChange={(e) => setPreserve(e.target.value)} />
          </label>
          <label>
            {t('Change requests')}
            <textarea value={changes} onChange={(e) => setChanges(e.target.value)} />
          </label>
          <p className="help">
            {t('Only this media is revised. Canonical story and Shot intent are preserved.')}
          </p>
          <button className="primary" disabled={disabled || (!feedback.trim() && !changes.trim())}>
            {t('Submit media feedback')}
          </button>
        </form>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
    </section>
  );
}
