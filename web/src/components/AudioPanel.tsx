import { useState } from 'react';
import type { Artifact, Schema, Snapshot, AudioProductionInput } from '../api/types';
import { API_BASE, api } from '../api/client';
import { downloadUrl } from './PostFilmPanel';

function AudioPreview({ artifact, label }: { artifact?: Artifact; label: string }) {
  const [failed, setFailed] = useState(false);
  if (!artifact) return <p className="help">待生成</p>;
  const project = artifact.provenance.project_id || '';
  const url = `${API_BASE}/artifacts/${encodeURIComponent(artifact.artifact_id)}/preview?project_id=${encodeURIComponent(project)}&version=${artifact.version}`;
  return (
    <div className="audio-preview">
      <audio controls preload="none" src={url} aria-label={label} onError={() => setFailed(true)} />
      {failed ? <p role="alert">音频预览失败，可下载后试听。</p> : null}
      <a download href={downloadUrl(project, artifact.artifact_id, artifact.version)}>
        下载音频 · v{artifact.version}
      </a>
    </div>
  );
}

type Command = AudioProductionInput;
type Run = (command: Command) => Promise<void>;

function VoiceInspector({
  character,
  profile,
  anchor,
  run,
  disabled,
}: {
  character: Schema['Character'];
  profile?: Schema['CharacterVoiceProfile'];
  anchor?: Artifact;
  run: Run;
  disabled: boolean;
}) {
  const [description, setDescription] = useState(
    profile?.design_instruction || character.voice_constraints?.join('；') || '',
  );
  return (
    <details className="audio-card" open>
      <summary>
        {character.name} · Voice Profile {profile ? `v${profile.version}` : '待设计'}
      </summary>
      {profile ? (
        <p>
          {profile.provider_id} · {profile.model_profile}
          <br />
          <small>{profile.reference_sha256.slice(0, 16)}…</small>
        </p>
      ) : null}
      <AudioPreview artifact={anchor} label={`试听角色声音 ${character.name}`} />
      <label>
        声音描述
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <button
        disabled={disabled || !description.trim()}
        onClick={() =>
          void run({
            action: 'redesign_voice',
            character_id: character.character_id,
            voice_description: description,
          })
        }
      >
        重新设计声音
      </button>
      <p className="help">新声音创建新版本，仅更新该角色对白和后期。</p>
    </details>
  );
}

function DialogueInspector({
  cue,
  artifact,
  character,
  run,
  disabled,
  status,
}: {
  cue: Schema['DialogueCue'];
  artifact?: Artifact;
  character: string;
  run: Run;
  disabled: boolean;
  status?: string;
}) {
  const [emotion, setEmotion] = useState(cue.emotion || '');
  const [pace, setPace] = useState<'slow' | 'medium' | 'fast'>(
    cue.pace === 'slow' || cue.pace === 'fast' ? cue.pace : 'medium',
  );
  return (
    <article className="audio-card">
      <strong>{character}</strong>
      <p>{cue.text}</p>
      <p className="help">
        {cue.voice_profile_id} · v{cue.voice_profile_version} ·{' '}
        {cue.target_start_seconds.toFixed(2)}–{cue.target_end_seconds.toFixed(2)} s<br />
        {status || (artifact ? 'succeeded' : '待生成')} ·{' '}
        {artifact ? `实际 ${Number(artifact.metadata.duration_seconds).toFixed(2)} s` : '等待音频'}
      </p>
      <AudioPreview artifact={artifact} label={`播放语音 ${cue.text}`} />
      <label>
        表演情绪
        <input value={emotion} onChange={(e) => setEmotion(e.target.value)} />
      </label>
      <label>
        语速意图
        <select value={pace} onChange={(e) => setPace(e.target.value as typeof pace)}>
          <option value="slow">慢</option>
          <option value="medium">自然</option>
          <option value="fast">快</option>
        </select>
      </label>
      <p className="help">
        当前 Base 克隆不原生控制情绪与语速；此处记录表演意图，生成后需试听。仅支持镜头 /
        台词级时间，不提供逐词口型同步。
      </p>
      <button
        disabled={disabled}
        onClick={() => void run({ action: 'regenerate_speech', cue_id: cue.cue_id, emotion, pace })}
      >
        重新生成这句语音
      </button>
    </article>
  );
}

export function AudioPanel({
  snapshot,
  shotId,
  onChanged,
}: {
  snapshot: Snapshot;
  shotId?: string;
  onChanged?: () => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [listeningNotes, setListeningNotes] = useState('');
  const disabled = busy || ['running', 'pausing', 'cancelled'].includes(snapshot.status);
  const real =
    snapshot.media_provider_ids?.includes('qwen3_tts') &&
    snapshot.media_provider_ids?.includes('ace_step');
  const run: Run = async (command) => {
    setBusy(true);
    setError('');
    try {
      await api.audio(snapshot.project.project_id, command);
      await onChanged?.();
    } catch (value) {
      setError(value instanceof Error ? value.message : '音频操作失败');
    } finally {
      setBusy(false);
    }
  };
  const selected = new Map(
    snapshot.artifacts.filter((a) => a.selected).map((a) => [a.artifact_id, a]),
  );
  const dialogue = (snapshot.dialogue_cues || []).filter((c) => !shotId || c.shot_id === shotId);
  const characters = snapshot.project.characters.filter(
    (c) => !shotId || dialogue.some((d) => d.character_id === c.character_id),
  );
  const musicControls = snapshot.audio_controls?.music as
    Record<string, { enabled?: boolean; intensity?: number }> | undefined;
  return (
    <section className="audio-production" aria-label="对白与配乐">
      <h2>对白与配乐</h2>
      {!real ? <p className="help">需要启用真实 TTS 与 Music 服务。</p> : null}
      <button
        className="primary"
        disabled={disabled || !real}
        onClick={() => void run({ action: 'produce' })}
      >
        生成对白与场景配乐
      </button>
      {error ? <p role="alert">{error}</p> : null}
      <h3>角色声音</h3>
      {characters.map((character) => {
        const profile = snapshot.voice_profiles?.find(
          (p) => p.character_id === character.character_id,
        );
        const anchor = snapshot.artifacts.find(
          (a) =>
            a.artifact_id === profile?.reference_artifact_id &&
            a.version === profile.reference_artifact_version,
        );
        return (
          <VoiceInspector
            key={`${character.character_id}:${profile?.version || 0}`}
            character={character}
            profile={profile}
            anchor={anchor}
            run={run}
            disabled={disabled || !real}
          />
        );
      })}
      <h3>Dialogue · 正式对白</h3>
      {dialogue.map((cue) => (
        <DialogueInspector
          key={`${cue.cue_id}:${cue.voice_profile_version}`}
          cue={cue}
          artifact={selected.get('speech_' + cue.cue_id)}
          character={
            snapshot.project.characters.find((c) => c.character_id === cue.character_id)?.name ||
            cue.character_id
          }
          status={
            snapshot.graph.nodes.find((n) => n.node_id === 'audio_speech:' + cue.cue_id)?.status
          }
          run={run}
          disabled={disabled || !real}
        />
      ))}
      <h3>Music · 场景配乐</h3>
      {snapshot.project.scenes
        .filter(
          (s) =>
            !shotId ||
            snapshot.project.shots.some(
              (shot) => shot.shot_id === shotId && shot.scene_id === s.scene_id,
            ),
        )
        .map((scene) => {
          const artifact = selected.get('music_' + scene.scene_id);
          const control = musicControls?.[scene.scene_id];
          return (
            <article className="audio-card" key={scene.scene_id}>
              <strong>{scene.title}</strong>
              {snapshot.project.brief.music_style ? (
                <p>{snapshot.project.brief.music_style}</p>
              ) : null}
              <p>{scene.purpose}</p>
              {artifact ? (
                <p>
                  {Number(artifact.metadata.duration_seconds).toFixed(2)} s ·{' '}
                  {String(artifact.metadata.sample_rate)} Hz
                </p>
              ) : null}
              <AudioPreview artifact={artifact} label={`播放配乐 ${scene.title}`} />
              <label>
                <input
                  type="checkbox"
                  checked={control?.enabled !== false}
                  disabled={disabled || !real}
                  onChange={(e) =>
                    void run({
                      action: 'music',
                      scene_id: scene.scene_id,
                      enabled: e.target.checked,
                    })
                  }
                />
                启用这段配乐
              </label>
              <label>
                配乐强度
                <select
                  value={control?.intensity ?? 0.35}
                  disabled={disabled || !real}
                  onChange={(e) =>
                    void run({
                      action: 'music',
                      scene_id: scene.scene_id,
                      intensity: Number(e.target.value),
                    })
                  }
                >
                  <option value={0.15}>轻</option>
                  <option value={0.35}>克制</option>
                  <option value={0.65}>强</option>
                </select>
              </label>
            </article>
          );
        })}
      <h3>Audio Timeline</h3>
      {(snapshot.timeline?.audio_tracks || []).map((track) => (
        <section key={track.track_id} className="audio-track">
          <strong>{track.name || track.track_id}</strong>
          <ol>
            {track.cues.map((cue) => (
              <li key={cue.cue_id}>
                {cue.start_time_seconds.toFixed(2)}–
                {(cue.start_time_seconds + cue.duration_seconds).toFixed(2)} s · {cue.cue_type} · v
                {cue.version}
              </li>
            ))}
          </ol>
        </section>
      ))}
      <p className="help">
        H3 原声用于环境与现场声，正式对白由独立 TTS 提供。清晰度、音色一致性和配乐无歌声需人工试听。
      </p>
      <label>
        人工试听记录
        <textarea
          value={listeningNotes}
          onChange={(e) => setListeningNotes(e.target.value)}
          placeholder="记录台词清晰度、角色音色及是否有歌声"
        />
      </label>
      <button
        disabled={disabled || !real || !listeningNotes.trim()}
        onClick={() =>
          void run({ action: 'listening_result', listening_passed: true, notes: listeningNotes })
        }
      >
        记录试听通过
      </button>
      <button
        disabled={disabled || !real || !listeningNotes.trim()}
        onClick={() =>
          void run({ action: 'listening_result', listening_passed: false, notes: listeningNotes })
        }
      >
        记录需要修改
      </button>
      {snapshot.audio_listening_result ? (
        <p>
          {snapshot.audio_listening_result.passed
            ? '最近一次人工试听记录：通过'
            : '最近一次人工试听记录：需要修改'}
          <br />
          {String(snapshot.audio_listening_result.notes || '')}
        </p>
      ) : (
        <p className="help">尚无人工试听结论</p>
      )}
    </section>
  );
}
