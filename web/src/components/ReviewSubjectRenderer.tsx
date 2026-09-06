import { useState } from 'react';
import type { Schema } from '../api/types';
import { t, useLocale } from '../i18n';

type Content = NonNullable<Schema['ReviewSource']['content']>;
function Section({ title, items }: { title: string; items: string[] }) {
  return (
    <section>
      <h4>{t(title)}</h4>
      {items.length ? (
        <ul>
          {items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="help">{t('Not provided')}</p>
      )}
    </section>
  );
}

// Exhaustive typed dispatch lives here, never in the Inspector or in a model call.
function TypedSummary({ content }: { content: Content }) {
  switch (content.kind) {
    case 'StoryBible': {
      const story = content.output;
      return (
        <>
          <Section title="Story synopsis" items={[story.synopsis]} />
          <Section title="Core themes" items={story.themes} />
          <Section title="Story structure" items={story.acts} />
          <Section
            title="Character arcs"
            items={Object.entries(story.character_arcs).map(([name, arc]) => `${name}: ${arc}`)}
          />
          <Section title="World rules" items={story.world_facts} />
          <Section title="Immutable facts" items={story.immutable_facts} />
        </>
      );
    }
    case 'CreativeDirection': {
      const direction = content.output;
      return (
        <>
          <Section
            title="Creative direction"
            items={[direction.premise_expansion, direction.emotional_arc, direction.tone]}
          />
          <Section title="Motifs" items={direction.motifs} />
          <Section title="Creative choices" items={direction.creative_choices} />
          <Section title="Preserved constraints" items={direction.preserved_user_constraints} />
        </>
      );
    }
    case 'Screenplay':
      return (
        <>
          <h4>{content.output.title}</h4>
          {content.output.scenes.map((scene) => (
            <section key={scene.scene_id}>
              <h4>{scene.title}</h4>
              <p className="mono">
                {scene.scene_id} · {scene.location_id}
              </p>
              <Section title="Key action" items={scene.action} />
              <Section
                title="Dialogue"
                items={scene.dialogue.map((line) => `${line.character_id}: ${line.text}`)}
              />
            </section>
          ))}
          <Section title="Preserved constraints" items={content.output.preserved_constraints} />
          <Section title="Immutable facts" items={content.output.immutable_facts} />
        </>
      );
    case 'ShotPlan': {
      const plan = content.output;
      return (
        <>
          <h4>
            {t('Scene')} · {plan.scene_id}
          </h4>
          <p>
            {t('Shot count')}: {plan.shots.length} · {t('Duration')}:{' '}
            {plan.shots.reduce((total, shot) => total + shot.duration_seconds, 0)} s
          </p>
          {plan.shots.map((shot) => (
            <section key={shot.shot_id}>
              <h4>{shot.narrative.beat}</h4>
              <p>{shot.narrative.purpose}</p>
              <p>{shot.narrative.action_summary}</p>
              <p>
                {t('Camera')}: {shot.camera.shot_size} · {shot.camera.angle} ·{' '}
                {shot.camera.motion.motion_type}
                {shot.camera.lens_mm ? ` · ${shot.camera.lens_mm} mm` : ''}
              </p>
              <p>
                {t('Duration')}: {shot.duration_seconds} s
              </p>
              <Section
                title="Performance"
                items={shot.performances.map((p) => `${p.character_id}: ${p.action}`)}
              />
              <Section title="Dialogue" items={shot.narrative.dialogue} />
            </section>
          ))}
          <Section title="Preserved constraints" items={plan.preserved_constraints} />
          <Section title="Immutable facts" items={plan.immutable_facts} />
        </>
      );
    }
  }
}

export function ReviewSubjectRenderer({
  subject,
  onSource,
}: {
  subject?: Schema['ReviewSubject'];
  onSource?: (id: string) => void;
}) {
  useLocale();
  const [raw, setRaw] = useState(false);
  if (!subject?.sources.length)
    return (
      <p role="status">
        {t(
          subject?.unavailable_reason ||
            'Review subject unavailable. No committed evidence is linked to this review.',
        )}
      </p>
    );
  return (
    <div className="review-subject">
      <div className="eyebrow">{t('Reviewing now')}</div>
      <h3>{t(subject.subject_title)}</h3>
      <div className="segmented" aria-label={t('Review content format')}>
        <button aria-pressed={!raw} onClick={() => setRaw(false)}>
          {t('Summary')}
        </button>
        <button aria-pressed={raw} onClick={() => setRaw(true)}>
          {t('Raw JSON')}
        </button>
      </div>
      {subject.sources.map((source, index) => {
        const result = source.role_result;
        const report =
          result?.validation_replays.at(-1)?.validation || result?.attempts.at(-1)?.validation;
        return (
          <section key={`${source.source_node_id}:${index}`}>
            {raw ? (
              <pre className="json-view" tabIndex={0} data-testid="review-output">
                {JSON.stringify(result ? result.output : source.artifacts, null, 2)}
              </pre>
            ) : source.content ? (
              <TypedSummary content={source.content} />
            ) : (
              <>
                <span className="badge mock">{t('MOCK')}</span>
                <p>{t('Placeholder artifact. No playable media has been generated.')}</p>
              </>
            )}
            {result && (
              <p className="help">
                {t('Committed')} · {result.target_schema} · {result.provider_id}
              </p>
            )}
            {report ? (
              <>
                <p>
                  {t(report.issues.length ? 'Validation issues' : 'Recorded validation passed')}
                </p>
                {report.issues.map((issue, i) => (
                  <p key={i}>
                    {issue.path}: {issue.message}
                  </p>
                ))}
                {report.unverified_constraints.length > 0 && (
                  <Section title="Unverified constraints" items={report.unverified_constraints} />
                )}
              </>
            ) : (
              <p className="help">{t('No role validation report')}</p>
            )}
            {source.evaluations?.map((evaluation) => (
              <section key={evaluation.evaluation_id}>
                <h4>
                  {t('Recorded evaluation')} · {evaluation.score}
                </h4>
                <p>{t(evaluation.passed ? 'Passed' : 'Validation issues')}</p>
                <p>{evaluation.summary}</p>
              </section>
            ))}
            <details>
              <summary>{t('Related artifacts')}</summary>
              {source.artifacts.map((a) => (
                <p className="mono" key={`${a.artifact_id}:${a.version}`}>
                  {a.artifact_id} · v{a.version} · {a.provenance.tool}
                </p>
              ))}
            </details>
            {onSource && (
              <button className="text-button" onClick={() => onSource(source.source_node_id)}>
                {t('View source node')}
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}
