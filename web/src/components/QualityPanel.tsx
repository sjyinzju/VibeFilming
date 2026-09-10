import { useState } from 'react';
import type { Snapshot } from '../api/types';
import { downloadUrl } from './PostFilmPanel';
import { t } from '../i18n';

const score = (value: number | null) => (value === null ? '—' : `${Math.round(value * 100)}`);

export function QualityPanel({ snapshot }: { snapshot: Snapshot }) {
  const [filter, setFilter] = useState('all');
  const film = snapshot.film_quality;
  const project = snapshot.project.project_id;
  const shots = (film?.shots ?? []).filter(
    (s) =>
      filter === 'all' ||
      (filter === 'blocking' && s.blocking_issue_ids.length > 0) ||
      (filter === 'human_review' &&
        (s.status === 'human_review' || s.human_review_items.length > 0)) ||
      (filter === 'low_score' && s.status !== 'accepted'),
  );
  return (
    <section aria-label={t('Quality dashboard')}>
      <h2>{t('Quality dashboard')}</h2>
      {film ? (
        <p>
          {film.accepted_seconds.toFixed(1)} s · {film.accepted_shots}/{snapshot.project.shots.length}{' '}
          {t('Accepted')} · {film.profile}
        </p>
      ) : (
        <p>{t('Quality evidence pending')}</p>
      )}
      <p className="help">{t('Human aesthetic and listening review pending')}</p>
      <label>
        {t('Quality filter')}
        <select value={filter} onChange={(event) => setFilter(event.target.value)}>
          <option value="all">{t('All shots')}</option>
          <option value="blocking">{t('Blocking issues')}</option>
          <option value="human_review">{t('Human review')}</option>
          <option value="low_score">{t('Low score')}</option>
        </select>
      </label>
      <div className="quality-grid">
        {shots.map((shot) => {
          const frames = snapshot.artifacts.filter(
            (a) =>
              ['first', 'last'].some((end) => a.artifact_id === `frame_${shot.shot_id}_${end}`) &&
              !a.metadata.mock,
          );
          const frame = frames.find((a) => a.selected);
          const history = snapshot.quality_history.filter((s) => s.shot_id === shot.shot_id);
          const inspection = snapshot.media_inspections.find((i) =>
            shot.inspection_ids.includes(i.result_id),
          );
          return (
            <article key={shot.shot_id} className="quality-card">
              {frame ? (
                <img
                  loading="lazy"
                  src={downloadUrl(project, frame.artifact_id, frame.version)}
                  alt={shot.shot_id}
                />
              ) : null}
              <h3>
                {shot.shot_id} · {shot.duration_seconds.toFixed(1)} s
              </h3>
              <p>
                {t(shot.status)} · v{shot.artifact_version}
              </p>
              <dl>
                {['character_identity', 'continuity', 'action_completion'].map((name) => (
                  <div key={name}>
                    <dt>{t(name)}</dt>
                    <dd>
                      {score(shot.dimensions[name]?.score ?? null)} ·{' '}
                      {t(shot.dimensions[name]?.status ?? 'unknown')}
                    </dd>
                  </div>
                ))}
                <div>
                  <dt>{t('Cinematic')}</dt>
                  <dd>{score(shot.cinematic_score)}</dd>
                </div>
                <div>
                  <dt>{t('Audio')}</dt>
                  <dd>{t(shot.dimensions.dialogue_intelligibility_status?.status ?? 'unknown')}</dd>
                </div>
              </dl>
              <a href={downloadUrl(project, shot.artifact_id, shot.artifact_version)}>
                {t('Preview exact version')}
              </a>
              {inspection?.issues.map((issue) => (
                <p key={issue.issue_id}>{issue.message}</p>
              ))}
              <details>
                <summary>{t('Visual identity packs')}</summary>
                {snapshot.identity_set?.references
                  .filter((ref) =>
                    inspection?.provenance.input_artifact_ids.includes(ref.artifact_id),
                  )
                  .map((ref) => (
                    <p key={`${ref.artifact_id}-${ref.reference_role}`}>
                      <a href={downloadUrl(project, ref.artifact_id, ref.version)}>
                        {ref.subject_id} · {ref.reference_role} · v{ref.version}
                      </a>
                    </p>
                  ))}
              </details>
              <details>
                <summary>
                  {t('Version comparison')} ({history.length})
                </summary>
                <div className="quality-versions">
                  {frames.map((image) => (
                    <section key={`${image.artifact_id}-${image.version}`}>
                      <p>
                        {image.artifact_id} · v{image.version}
                      </p>
                      <img
                        loading="lazy"
                        src={downloadUrl(project, image.artifact_id, image.version)}
                        alt={`${image.artifact_id} v${image.version}`}
                      />
                      <p>
                        {snapshot.media_inspections
                          .filter(
                            (i) =>
                              i.target_artifact_id === image.artifact_id &&
                              i.target_artifact_version === image.version,
                          )
                          .map((i) => i.summary)
                          .join(' ')}
                      </p>
                    </section>
                  ))}
                </div>
                <div className="quality-versions">
                  {history.map((version, index) => (
                    <section key={`${version.artifact_version}-${index}`}>
                      <p>
                        v{version.artifact_version} · {t('Identity')}{' '}
                        {score(version.dimensions.character_identity?.score ?? null)}
                      </p>
                      <video
                        controls
                        preload="none"
                        src={downloadUrl(project, version.artifact_id, version.artifact_version)}
                      />
                      <p>
                        {snapshot.media_inspections
                          .filter((i) => version.inspection_ids.includes(i.result_id))
                          .map((i) => i.summary)
                          .join(' ')}
                      </p>
                    </section>
                  ))}
                </div>
              </details>
            </article>
          );
        })}
      </div>
      {(filter === 'all' || filter === 'human_review') &&
        snapshot.project.shots
          .filter((s) => !film?.shots.some((r) => r.shot_id === s.shot_id))
          .map((shot) => (
            <article className="quality-card" key={shot.shot_id}>
              <h3>{shot.shot_id}</h3>
              <p>{t('Quality evidence pending')}</p>
              {snapshot.media_inspections
                .filter((i) => i.shot_id === shot.shot_id)
                .map((i) => (
                  <p key={i.result_id}>
                    {t(i.decision)} · {i.summary}
                  </p>
                ))}
            </article>
          ))}
      <h3>{t('Visual identity packs')}</h3>
      <div className="quality-grid">
        {snapshot.identity_set?.references.map((ref) => (
          <article key={`${ref.subject_id}-${ref.reference_role}`} className="quality-card">
            <img
              loading="lazy"
              src={downloadUrl(project, ref.artifact_id, ref.version)}
              alt={`${ref.subject_id} ${ref.reference_role}`}
            />
            <p>
              {ref.subject_id} · {ref.reference_role} · v{ref.version}
            </p>
            <p>
              {ref.selected ? t('Selected') : t('Human review')} · {score(ref.score)}
            </p>
            <p>
              {
                snapshot.voice_profiles.find((v) => v.character_id === ref.subject_id)
                  ?.design_instruction
              }
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}
