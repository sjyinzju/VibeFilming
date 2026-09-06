import { t, useLocale } from './i18n';
import { LanguageSwitch } from './components/LanguageSwitch';
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Aperture,
  Plus,
  PanelLeftClose,
  PanelRightOpen,
  Play,
  Pause,
  Square,
  ArrowRight,
  X,
} from 'lucide-react';
import { api } from './api/client';
import type { Review } from './api/types';
import { useStudio } from './state/useStudio';
import { projectProgress } from './state/workflow';
import { load, save } from './state/storage';
import {
  BriefConsole,
  emptyDraft,
  toInput,
  validateBrief,
  type Draft,
} from './components/BriefConsole';
import { WorkflowCanvas } from './components/WorkflowCanvas';
import { Inspector } from './components/Inspector';
import { InferenceStatus, isConnectionError, hasInvalidOutput } from './components/InferenceStatus';

export default function App() {
  const language = useLocale();
  const [pid, setPid] = useState<string | null>(() =>
    new URLSearchParams(location.search).get('project'),
  );
  const [draft, setDraft] = useState<Draft>(() => load('draft', emptyDraft));
  const [left, setLeft] = useState(true);
  const [right, setRight] = useState(false);
  const [tab, setTab] = useState('Node');
  const [selected, setSelected] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<{ id: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [commandConnectionError, setCommandConnectionError] = useState(false);
  const [notice, setNotice] = useState('');
  const highWater = useRef({ pid: null as string | null, value: 0 });
  const openedReviews = useRef(new Set<string>());
  const studio = useStudio(pid);
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects, retry: false });
  const snapshot = studio.data;
  if (highWater.current.pid !== pid) highWater.current = { pid, value: load(`progress:${pid}`, 0) };
  const progress = snapshot
    ? projectProgress(snapshot, highWater.current.value)
    : { value: 0, complete: 0, total: 0, stage: 'Ready when you are' };
  useEffect(() => {
    if (!snapshot) return;
    highWater.current.value = progress.value;
    if (pid) save(`progress:${pid}`, progress.value);
  }, [pid, progress.value, !!snapshot]);
  useEffect(() => {
    save('draft', draft);
  }, [draft]);
  useEffect(() => {
    const pending = snapshot?.reviews.find(
      (r) => r.status === 'pending' && !openedReviews.current.has(r.review_id),
    );
    if (pending) {
      openedReviews.current.add(pending.review_id);
      setSelected(pending.node_id);
      setRight(true);
      setTab('Node');
    }
  }, [snapshot?.reviews]);
  useEffect(() => {
    const changed = () => {
      setPid(new URLSearchParams(location.search).get('project'));
      setSelected(null);
    };
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  const openProject = (next: string | null) => {
    const url = new URL(location.href);
    if (next) url.searchParams.set('project', next);
    else url.searchParams.delete('project');
    history.pushState({}, '', url);
    setPid(next);
    setSelected(null);
    setError('');
    setNotice('');
  };
  const perform = async (action: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await action();
      await studio.refresh();
      void projects.refetch();
    } catch (e) {
      setCommandConnectionError(isConnectionError(e));
      setError(e instanceof Error ? e.message : 'Something went wrong. Please try again.');
    } finally {
      setBusy(false);
    }
  };
  const start = () => {
    const invalid = validateBrief(draft);
    if (invalid) {
      setError(invalid);
      return;
    }
    void perform(async () => {
      const record = await api.create(toInput(draft, language));
      const next = record.project.project_id;
      // Keep the created project discoverable even if start fails; never create a duplicate retry.
      openProject(next);
      await api.command(next, 'start');
    });
  };
  const command = (action: 'start' | 'pause' | 'resume' | 'cancel') =>
    void perform(async () => {
      if (!pid) return;
      await api.command(pid, action);
      if (action === 'pause')
        setNotice('Pause requested. The current node will finish before production pauses.');
      if (action === 'cancel')
        setNotice(
          'Local production cancelled and checkpoint retained. Remote requests may finish independently.',
        );
    });
  const resolve = async (review: Review, approved: boolean, notes: string) => {
    await perform(async () => {
      await api.resolve(review.review_id, approved, notes);
      setNotice(
        approved
          ? 'Review approved.'
          : 'Revision requested. Your feedback is saved; production is held.',
      );
      if (approved && pid) await api.command(pid, 'resume');
    });
  };
  const unresolved = snapshot?.reviews.some((r) => r.status !== 'approved');
  const resumeEnabled =
    snapshot &&
    ['paused', 'failed', 'waiting_human'].includes(snapshot.status) &&
    !unresolved &&
    !hasInvalidOutput(snapshot);
  const replan = () =>
    void perform(async () => {
      if (pid && snapshot?.terminal_revision_scene_id)
        await api.reviseTerminal(pid, snapshot.terminal_revision_scene_id);
    });
  const visibleBrief = snapshot
    ? { ...snapshot.project.brief, creative_hints: snapshot.creative_hints }
    : draft;
  return (
    <div className="studio-shell">
      <header className="toolbar">
        <LanguageSwitch language={language} />
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            openProject(null);
          }}
        >
          <Aperture size={26} strokeWidth={1.6} />
          <span>
            {t(' movie agent')}
            <small>{t('WEB STUDIO')}</small>
          </span>
        </a>
        <div className="project-title">
          <span className="eyebrow">{t(pid ? 'CURRENT PRODUCTION' : 'NEW PRODUCTION')}</span>
          <strong>{snapshot?.project.brief.title || t('Your next film')}</strong>
        </div>
        <div className={`connection ${studio.connection.toLowerCase()}`} role="status">
          <i />
          {t(studio.connection)}
        </div>
        <select
          aria-label={t('Open project')}
          value={pid || ''}
          onChange={(e) => openProject(e.target.value || null)}
        >
          <option value="">{t('New film')}</option>
          {projects.data?.map((p) => (
            <option key={p.project.project_id} value={p.project.project_id}>
              {p.project.brief.title} · {p.project.project_id.slice(-6)}
            </option>
          ))}
        </select>
        <button
          title={t('New film')}
          aria-label={t('New film')}
          className="icon-button"
          onClick={() => openProject(null)}
        >
          <Plus size={18} />
        </button>
        <div className="lifecycle">
          <button
            disabled={busy || !snapshot || snapshot.status !== 'created'}
            onClick={() => command('start')}
          >
            <Play size={14} />
            {t(' Start ')}
          </button>
          <button
            disabled={busy || snapshot?.status !== 'running'}
            onClick={() => command('pause')}
          >
            <Pause size={14} />
            {t(' Pause ')}
          </button>
          <button disabled={busy || !resumeEnabled} onClick={() => command('resume')}>
            <Play size={14} />
            {t(
              snapshot?.status === 'failed' && !hasInvalidOutput(snapshot)
                ? 'Re-execute current planning'
                : ' Resume ',
            )}
          </button>
          <button
            disabled={busy || !snapshot || ['completed', 'cancelled'].includes(snapshot.status)}
            onClick={() => command('cancel')}
          >
            <Square size={13} />
            {t(' Cancel ')}
          </button>
        </div>
      </header>
      <div className="progress-strip">
        <button
          className="icon-button"
          aria-label={t('Toggle creative brief')}
          onClick={() => setLeft(!left)}
        >
          <PanelLeftClose size={17} />
        </button>
        <span className="eyebrow">{t('WORKFLOW COMPLETION')}</span>
        <div
          className="progress-track"
          role="progressbar"
          aria-label={t('Workflow completion')}
          aria-valuenow={Math.round(progress.value)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div style={{ width: `${progress.value}%` }} />
        </div>
        <span className="stage-label">{t(progress.stage)}</span>
        <span className="node-count">
          {progress.complete} / {progress.total}
          {t(' nodes ')}
        </span>
        <button
          className="icon-button"
          aria-label={t('Open inspector')}
          onClick={() => setRight(!right)}
        >
          <PanelRightOpen size={18} />
        </button>
      </div>
      <InferenceStatus
        snapshot={snapshot}
        busy={busy}
        canResume={!!resumeEnabled}
        onResume={() => command('resume')}
        onReplan={unresolved ? undefined : replan}
      />
      {(error || studio.error || projects.error) && (
        <div className="message error" role="alert">
          {t(error || studio.error?.message || projects.error?.message)}
          <button
            onClick={() => {
              setError('');
              if (
                commandConnectionError ||
                isConnectionError(studio.error) ||
                isConnectionError(projects.error)
              ) {
                if (pid) void studio.refetch();
                void projects.refetch();
              }
            }}
          >
            {t(
              commandConnectionError ||
                isConnectionError(studio.error) ||
                isConnectionError(projects.error)
                ? 'Retry connection'
                : 'Dismiss notification',
            )}
          </button>
        </div>
      )}
      {notice && (
        <div className="message" role="status">
          {t(notice)}
          <button
            className="icon-button"
            aria-label={t('Dismiss notification')}
            onClick={() => setNotice('')}
          >
            <X size={14} />
          </button>
        </div>
      )}
      <main className={`workspace ${left ? 'has-brief' : ''} ${right ? 'has-inspector' : ''}`}>
        {left && (
          <BriefConsole
            draft={visibleBrief}
            onChange={setDraft}
            locked={!!pid}
            busy={busy}
            onStart={start}
          />
        )}
        <section className="canvas-region" aria-label={t('Workflow canvas')}>
          <div className="canvas-heading">
            <div>
              <span className="eyebrow">{t('02 / PRODUCTION CANVAS')}</span>
              <h1>
                {t(
                  pid
                    ? 'The production, unfolding.'
                    : 'A little direction.\nA world of possibility.',
                )}
              </h1>
            </div>
            <span className="phase-badge">
              {t(' P3 ')}
              <span>{t('MEDIA FOUNDATION')}</span>
            </span>
          </div>
          {snapshot ? (
            <div className="flow-container">
              <WorkflowCanvas
                snapshot={snapshot}
                focusRequest={focusRequest}
                onSelect={(id) => {
                  setSelected(id);
                  setTab('Node');
                  setRight(true);
                }}
              />
            </div>
          ) : (
            <div className="canvas-empty">
              <div className="empty-mark">
                <Aperture size={46} strokeWidth={1} />
              </div>
              <h2>{t(pid ? 'Opening your production…' : 'Every film starts somewhere.')}</h2>
              <p>
                {t(
                  pid
                    ? 'Restoring the graph and durable production state.'
                    : 'Write the first thought. Your creative team will turn it into a production, one considered step at a time.',
                )}
              </p>
              {!pid && (
                <button
                  className="text-button"
                  onClick={() => {
                    setLeft(true);
                    document.getElementById('story_description')?.focus();
                  }}
                >
                  {t(' Start with your story ')}
                  <ArrowRight size={15} />
                </button>
              )}
            </div>
          )}
          <div className="canvas-footer">
            <span>
              <i className="legend-dot" />
              {t(' Waiting ')}
              <i className="legend-dot accent" />
              {t(' Running ')}
              <i className="legend-dot amber" />
              {t(' Human review ')}
            </span>
            <span>
              {studio.recovered > 0 ? `Recovered ${studio.recovered} events · ` : ''}
              {t('Snapshot + durable events ')}
            </span>
          </div>
        </section>
        {right && (
          <Inspector
            snapshot={snapshot}
            selected={selected}
            tab={tab}
            setTab={setTab}
            close={() => setRight(false)}
            onSource={(id) => {
              setSelected(id);
              setTab('Node');
              setFocusRequest({ id });
            }}
            onResolve={resolve}
            busy={busy}
          />
        )}
      </main>
    </div>
  );
}
