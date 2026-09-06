import { t, useLocale } from '../i18n';
import { useState } from 'react';
import { Plus, Trash2, ArrowUpRight } from 'lucide-react';
import schema from '../api/brief.schema.json';
import type { CreateInput, Hints } from '../api/types';

export type Draft = Partial<CreateInput>;
type FieldSchema = {
  title?: string;
  type?: string;
  default?: unknown;
  $ref?: string;
  minimum?: number;
  exclusiveMinimum?: number;
  maximum?: number;
  minLength?: number;
  items?: { type?: string };
};
export const fields = schema.properties as Record<string, FieldSchema>;
export const fieldLabel = (key: string) =>
  key === 'story_description'
    ? 'Your story'
    : key === 'target_duration'
      ? 'Target duration · seconds'
      : fields[key].title || key.replaceAll('_', ' ').replace(/^./, (s) => s.toUpperCase());
const defs = schema.$defs as Record<string, { enum: string[] }>;
export const emptyDraft: Draft = { story_description: '', target_duration: 30 };
const groups: Record<string, string[]> = {
  Basic: [
    'title',
    'logline',
    'story_description',
    'target_duration',
    'output_language',
    'genre',
    'audience',
    'rating',
  ],
  Story: [
    'theme',
    'narrative_style',
    'ending_preference',
    'pacing',
    'dialogue_density',
    'creative_freedom',
  ],
  Characters: ['desired_character_count', 'character_descriptions', 'relationship_constraints'],
  World: ['locations', 'time_period', 'sci_fi_setting', 'world_rules', 'prohibited_elements'],
  Visual: [
    'visual_style',
    'reference_movies',
    'reference_images',
    'palette',
    'lighting',
    'realism',
    'aspect_ratio',
    'resolution',
    'fps',
  ],
  Cinematography: [
    'preferred_shot_style',
    'preferred_camera_motion',
    'preferred_lenses',
    'cutting_style',
    'composition_preferences',
  ],
  Audio: ['dialogue_style', 'voice_style', 'music_style', 'ambience_style', 'sound_design'],
  Production: ['quality_level', 'max_shots', 'max_retry', 'generation_budget'],
  Constraints: ['user_constraints', 'must_preserve'],
};
const suggestions: Record<string, string[]> = {
  genre: ['Drama', 'Science fiction', 'Mystery', 'Comedy', 'Documentary'],
  palette: ['Warm neutrals', 'Earth tones', 'Monochrome', 'Muted greens'],
  preferred_camera_motion: ['static', 'pan', 'dolly', 'track', 'handheld', 'orbit'],
};
export function validateBrief(draft: Draft): string | null {
  if (!draft.story_description?.trim()) return 'Tell us your story to begin.';
  const hints = draft.creative_hints;
  if (hints?.scene_seeds?.some((s) => !s.title.trim() || !s.description.trim()))
    return 'Give each scene seed a title and description, or remove it.';
  if (
    [...(hints?.key_moments || []), ...(hints?.key_visuals || [])].some(
      (h) => !h.description.trim(),
    )
  )
    return 'Describe each creative hint, or remove the empty card.';
  if (hints?.style_references?.some((r) => !r.title.trim()))
    return 'Name each reference work, or remove the empty reference.';
  for (const [key, value] of Object.entries(draft)) {
    const field = fields[key];
    if (!field || value === undefined) continue;
    if (field.minLength && typeof value === 'string' && !value.trim())
      return `${field.title} cannot be empty.`;
    if (
      typeof value === 'number' &&
      (!Number.isFinite(value) ||
        (field.type === 'integer' && !Number.isInteger(value)) ||
        (field.minimum !== undefined && value < field.minimum) ||
        (field.exclusiveMinimum !== undefined && value <= field.exclusiveMinimum) ||
        (field.maximum !== undefined && value > field.maximum))
    )
      return `Check ${field.title?.toLowerCase()}.`;
    if (
      key === 'preferred_lenses' &&
      Array.isArray(value) &&
      value.some((n) => typeof n !== 'number' || !Number.isFinite(n) || n <= 0)
    )
      return 'Lens focal lengths must be positive numbers.';
  }
  return null;
}
export function toInput(draft: Draft): CreateInput {
  const defaults = Object.fromEntries(
    Object.entries(fields)
      .filter(([, field]) => field.default !== undefined)
      .map(([key, field]) => [key, field.default]),
  );
  return {
    ...defaults,
    ...draft,
    title: draft.title || 'Untitled film',
    logline: draft.logline || 'A story waiting to unfold.',
    target_duration: draft.target_duration ?? 30,
  } as CreateInput;
}
function TagInput({
  value,
  onChange,
  choices = [],
  label,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  choices?: string[];
  label: string;
}) {
  const [text, setText] = useState('');
  const add = () => {
    if (text.trim())
      onChange([
        ...new Set([
          ...value,
          ...text
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean),
        ]),
      ]);
    setText('');
  };
  return (
    <div className="tag-control">
      <div className="tags">
        {value.map((tag, i) => (
          <button
            type="button"
            className="chip chosen"
            key={`${tag}-${i}`}
            aria-label={t(`Remove ${tag}`)}
            onClick={() => onChange(value.filter((_, j) => i !== j))}
          >
            {tag} ×
          </button>
        ))}
      </div>
      <input
        aria-label={t(label)}
        placeholder={t('Type and press Enter')}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={add}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            add();
          }
        }}
      />
      {choices.length > 0 && (
        <div className="tags suggestions">
          {choices
            .filter((c) => !value.includes(c))
            .map((c) => (
              <button
                type="button"
                key={c}
                className="chip"
                onClick={() => onChange([...value, c])}
              >
                + {t(c)}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}
function Cards({
  value,
  onChange,
  label,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  label: string;
}) {
  return (
    <div>
      {value.map((item, i) => (
        <div className="input-card" key={i}>
          <div className="row">
            <small>
              {t(label)} {String(i + 1).padStart(2, '0')}
            </small>
            <button
              type="button"
              className="icon-button"
              aria-label={t(`Remove ${label} ${i + 1}`)}
              onClick={() => onChange(value.filter((_, j) => i !== j))}
            >
              <Trash2 size={14} />
            </button>
          </div>
          <textarea
            aria-label={t(`${label} ${i + 1}`)}
            value={item}
            placeholder={t('Name, motivation, defining details…')}
            onChange={(e) => onChange(value.map((v, j) => (i === j ? e.target.value : v)))}
          />
        </div>
      ))}
      <button type="button" className="text-button" onClick={() => onChange([...value, ''])}>
        <Plus size={14} />
        {t(' Add ')}
        {t(label.toLowerCase())}
      </button>
    </div>
  );
}

export function BriefConsole({
  draft,
  onChange,
  locked,
  busy,
  onStart,
}: {
  draft: Draft;
  onChange: (d: Draft) => void;
  locked: boolean;
  busy: boolean;
  onStart: () => void;
}) {
  useLocale();
  const [advanced, setAdvanced] = useState(false);
  const [mode, setMode] = useState('Story-first');
  const update = (key: string, value: unknown) => onChange({ ...draft, [key]: value });
  const renderField = (key: string) => {
    const field = fields[key];
    const value =
      (draft as Record<string, unknown>)[key] ??
      field.default ??
      (field.type === 'array' ? [] : '');
    const label = fieldLabel(key);
    const values = field.$ref ? defs[field.$ref.split('/').at(-1)!]?.enum : undefined;
    let control;
    if (key === 'character_descriptions')
      control = (
        <Cards label="Character" value={value as string[]} onChange={(v) => update(key, v)} />
      );
    else if (field.type === 'array')
      control = (
        <TagInput
          label={label}
          value={(value as unknown[]).map(String)}
          choices={suggestions[key]}
          onChange={(v) => update(key, field.items?.type === 'number' ? v.map(Number) : v)}
        />
      );
    else if (values)
      control = (
        <div
          className={`segmented ${key === 'aspect_ratio' ? 'ratios' : ''}`}
          role="group"
          aria-label={t(label)}
        >
          {values.map((v) => (
            <button type="button" aria-pressed={value === v} key={v} onClick={() => update(key, v)}>
              {t(v.replaceAll('_', ' '))}
            </button>
          ))}
        </div>
      );
    else if (field.type === 'boolean')
      control = (
        <input
          id={key}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => update(key, e.target.checked)}
        />
      );
    else if (key === 'dialogue_density' || key === 'target_duration')
      control = (
        <div className="range">
          <input
            id={key}
            type="range"
            min={key === 'target_duration' ? 6 : 0}
            max={key === 'target_duration' ? Math.max(180, Number(value)) : 1}
            step={key === 'target_duration' ? 1 : 0.05}
            value={Number(value)}
            onChange={(e) => update(key, Number(e.target.value))}
          />
          <output>
            {value as number}
            {t(key === 'target_duration' ? 's' : '')}
          </output>
        </div>
      );
    else if (key === 'fps')
      control = (
        <select
          id={key}
          value={String(value)}
          onChange={(e) => update(key, Number(e.target.value))}
        >
          {[...new Set([24, 25, 30, 48, 60, Number(value)])].map((v) => (
            <option key={v} value={v}>
              {v}
              {t(' fps ')}
            </option>
          ))}
        </select>
      );
    else if (field.type === 'number' || field.type === 'integer')
      control = (
        <input
          id={key}
          type="number"
          min={field.minimum ?? field.exclusiveMinimum}
          step={field.type === 'integer' ? 1 : 'any'}
          value={String(value)}
          onChange={(e) => update(key, e.target.valueAsNumber)}
        />
      );
    else if (key === 'story_description' || key === 'logline')
      control = (
        <textarea
          id={key}
          rows={key === 'story_description' ? 7 : 2}
          placeholder={t(
            key === 'story_description'
              ? 'A lone archivist discovers a message recorded tomorrow…'
              : 'The heart of your film, in one sentence.',
          )}
          value={String(value)}
          onChange={(e) => update(key, e.target.value)}
        />
      );
    else
      control = (
        <input
          id={key}
          value={String(value)}
          placeholder={t(key === 'title' ? 'Untitled film' : 'Optional')}
          onChange={(e) => update(key, e.target.value)}
        />
      );
    return (
      <div className="field" key={key}>
        <label htmlFor={key}>
          {t(label)}
          {key === 'story_description' && <span className="required">{t(' required')}</span>}
        </label>
        {control}
      </div>
    );
  };
  return (
    <aside className="brief-console">
      <div className="panel-heading">
        <div className="eyebrow">{t('01 / CREATIVE CONSOLE')}</div>
        <h2>{t('Begin with a story.')}</h2>
        <p>
          {t(' Give your film a direction. ')}
          <br />
          {t(' Leave room for discovery. ')}
        </p>
      </div>
      <div className="mode-tabs" role="group" aria-label={t('Input mode')}>
        {['Story-first', 'Scene-guided', 'Key moments'].map((m) => (
          <button type="button" key={m} aria-pressed={mode === m} onClick={() => setMode(m)}>
            {t(m)}
          </button>
        ))}
      </div>
      <div className="row disclosure">
        <span>{t(locked ? 'Submitted brief' : 'Draft · saved locally')}</span>
        <button type="button" onClick={() => setAdvanced(!advanced)}>
          {t(advanced ? 'Quick view' : 'Advanced')}
        </button>
      </div>
      <fieldset disabled={locked || busy}>
        {advanced ? (
          Object.entries(groups).map(([group, keys], i) => (
            <details key={group} open={i === 0}>
              <summary>
                {t(group)}
                <span>{keys.length}</span>
              </summary>
              <div className="section-fields">{keys.map(renderField)}</div>
            </details>
          ))
        ) : (
          <div className="section-fields">
            {['title', 'story_description', 'target_duration', 'visual_style'].map(renderField)}
          </div>
        )}
        <HintsEditor
          hints={draft.creative_hints || {}}
          onChange={(v) => update('creative_hints', v)}
          expanded={mode !== 'Story-first'}
        />
      </fieldset>
      <div className="brief-footer">
        {locked ? (
          <p>{t('Canonical project input · read only')}</p>
        ) : (
          <>
            <button className="primary start-film" disabled={busy} onClick={onStart}>
              {t(' Start Film ')}
              <ArrowUpRight size={17} />
            </button>
            <p>
              {t(' Reasoning through Movie Agent. ')}
              <br />
              {t(' Media production remains Mock. ')}
            </p>
          </>
        )}
      </div>
    </aside>
  );
}

function HintsEditor({
  hints,
  onChange,
  expanded,
}: {
  hints: Hints;
  onChange: (h: Hints) => void;
  expanded: boolean;
}) {
  return (
    <details open={expanded || undefined} className="hints">
      <summary>
        {t(' Creative guidance')}
        <span>{t('Optional')}</span>
      </summary>
      <div className="section-fields">
        <p className="help">
          {t(
            ' Mix scene seeds, essential moments and visual inspiration. These are user-authored hints, not final shots. ',
          )}
        </p>
        <label>{t('Scene seeds')}</label>
        {(hints.scene_seeds || []).map((s, i) => (
          <div className="input-card" key={i}>
            <div className="row">
              <small>
                {t('SCENE SEED ')}
                {i + 1}
              </small>
              <button
                type="button"
                className="icon-button"
                aria-label={t(`Remove scene seed ${i + 1}`)}
                onClick={() =>
                  onChange({ ...hints, scene_seeds: hints.scene_seeds!.filter((_, j) => i !== j) })
                }
              >
                <Trash2 size={14} />
              </button>
            </div>
            <input
              aria-label={t(`Scene seed ${i + 1} title`)}
              placeholder={t('Scene title')}
              value={s.title}
              onChange={(e) =>
                onChange({
                  ...hints,
                  scene_seeds: hints.scene_seeds!.map((v, j) =>
                    j === i ? { ...v, title: e.target.value } : v,
                  ),
                })
              }
            />
            <textarea
              aria-label={t(`Scene seed ${i + 1} description`)}
              placeholder={t('What happens here?')}
              value={s.description}
              onChange={(e) =>
                onChange({
                  ...hints,
                  scene_seeds: hints.scene_seeds!.map((v, j) =>
                    j === i ? { ...v, description: e.target.value } : v,
                  ),
                })
              }
            />
          </div>
        ))}
        <button
          type="button"
          className="text-button"
          onClick={() =>
            onChange({
              ...hints,
              scene_seeds: [...(hints.scene_seeds || []), { title: '', description: '' }],
            })
          }
        >
          <Plus size={14} />
          {t(' Add scene seed ')}
        </button>
        <label>{t('Key moments')}</label>
        <Cards
          label="Moment"
          value={(hints.key_moments || []).map((m) => m.description)}
          onChange={(v) =>
            onChange({ ...hints, key_moments: v.map((description) => ({ description })) })
          }
        />
        <label>{t('Key visuals')}</label>
        <Cards
          label="Visual"
          value={(hints.key_visuals || []).map((m) => m.description)}
          onChange={(v) =>
            onChange({ ...hints, key_visuals: v.map((description) => ({ description })) })
          }
        />
        <label>{t('Reference works')}</label>
        <p className="help">{t('Inspiration for tone and craft, never a request to copy.')}</p>
        {(hints.style_references || []).map((ref, i) => (
          <div className="input-card" key={i}>
            <div className="row">
              <select
                aria-label={t(`Reference ${i + 1} kind`)}
                value={ref.kind || 'film'}
                onChange={(e) =>
                  onChange({
                    ...hints,
                    style_references: hints.style_references!.map((v, j) =>
                      j === i ? { ...v, kind: e.target.value as typeof ref.kind } : v,
                    ),
                  })
                }
              >
                {['film', 'series', 'director', 'photography', 'visual'].map((v) => (
                  <option key={v} value={v}>
                    {t(v)}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="icon-button"
                aria-label={t(`Remove reference ${i + 1}`)}
                onClick={() =>
                  onChange({
                    ...hints,
                    style_references: hints.style_references!.filter((_, j) => j !== i),
                  })
                }
              >
                <Trash2 size={14} />
              </button>
            </div>
            <input
              aria-label={t(`Reference ${i + 1} title`)}
              placeholder={t('e.g. Arrival')}
              value={ref.title}
              onChange={(e) =>
                onChange({
                  ...hints,
                  style_references: hints.style_references!.map((v, j) =>
                    j === i ? { ...v, title: e.target.value } : v,
                  ),
                })
              }
            />
            <div className="tags">
              {(
                [
                  'visual',
                  'lighting',
                  'cinematography',
                  'color',
                  'editing_rhythm',
                  'narrative_tone',
                ] as const
              ).map((d) => (
                <button
                  key={d}
                  type="button"
                  className={`chip ${ref.dimensions?.includes(d) ? 'chosen' : ''}`}
                  aria-pressed={!!ref.dimensions?.includes(d)}
                  onClick={() =>
                    onChange({
                      ...hints,
                      style_references: hints.style_references!.map((v, j) =>
                        j === i
                          ? {
                              ...v,
                              dimensions: ref.dimensions?.includes(d)
                                ? ref.dimensions.filter((x) => x !== d)
                                : [...(ref.dimensions || []), d],
                            }
                          : v,
                      ),
                    })
                  }
                >
                  {t(d.replaceAll('_', ' '))}
                </button>
              ))}
            </div>
          </div>
        ))}
        <button
          type="button"
          className="text-button"
          onClick={() =>
            onChange({
              ...hints,
              style_references: [
                ...(hints.style_references || []),
                { title: '', kind: 'film', dimensions: [] },
              ],
            })
          }
        >
          <Plus size={14} />
          {t(' Add reference ')}
        </button>
      </div>
    </details>
  );
}
