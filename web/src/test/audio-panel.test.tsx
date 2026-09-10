import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AudioPanel } from '../components/AudioPanel';
import { api } from '../api/client';
import type { Snapshot, Artifact } from '../api/types';
import data from './snapshot.json';

function fixture() {
  const snapshot = structuredClone(data) as unknown as Snapshot;
  snapshot.status = 'completed';
  snapshot.media_provider_ids = ['qwen3_tts', 'ace_step', 'ffmpeg-post'];
  snapshot.project.characters = [
    {
      schema_version: '1.0.0',
      character_id: 'char',
      name: '林恩',
      narrative_role: '',
      identity_description: '',
      personality: [],
      appearance_constraints: [],
      voice_constraints: [],
      reference_artifact_ids: [],
    },
  ];
  snapshot.project.scenes = [
    {
      scene_id: 'scene',
      title: '舱内',
      purpose: '克制的紧张感',
    } as Snapshot['project']['scenes'][number],
  ];
  const character = snapshot.project.characters[0];
  const scene = snapshot.project.scenes[0];
  const shot = { shot_id: 'shot' };
  snapshot.voice_profiles = [
    {
      schema_version: '1.0.0',
      voice_profile_id: 'voice',
      version: 1,
      character_id: character.character_id,
      project_id: snapshot.project.project_id,
      design_instruction: '冷静克制',
      reference_artifact_id: 'anchor',
      reference_artifact_version: 1,
      reference_sha256: 'a'.repeat(64),
      reference_text: '参考声音',
      language: 'zh',
      provider_id: 'qwen3_tts',
      model_profile: 'VoiceDesign → Base',
      created_at: new Date().toISOString(),
    },
  ];
  snapshot.dialogue_cues = [
    {
      schema_version: '1.0.0',
      cue_id: 'cue',
      project_id: snapshot.project.project_id,
      scene_id: scene.scene_id,
      shot_id: shot.shot_id,
      character_id: character.character_id,
      text: '林恩，不要靠近地面。',
      voice_profile_id: 'voice',
      voice_profile_version: 1,
      target_start_seconds: 0.4,
      target_end_seconds: 4,
      emotion: '克制',
      pace: 'medium',
      prosody: [],
      language: 'zh',
      overlapping_speech: false,
    },
  ];
  const artifact = (id: string): Artifact => ({
    schema_version: '1.0.0',
    artifact_id: id,
    version: 1,
    artifact_type: 'audio',
    uri: `artifact://${id}/v1`,
    selected: true,
    source_job_id: null,
    parent_artifact_ids: [],
    created_at: new Date().toISOString(),
    provenance: {
      schema_version: '1.0.0',
      project_id: snapshot.project.project_id,
      role: null,
      tool: null,
      provider_id: 'qwen3_tts',
      model_service_id: 'tts',
      prompt_package_id: null,
      compiler_id: null,
      compiler_version: null,
      scene_id: null,
      shot_id: null,
      input_artifact_ids: [],
      generation_strategy: null,
      seed: null,
      parameters: {},
      retry_history: [],
      evaluation_ids: [],
      repair_plan_ids: [],
      parent_artifact_id: null,
    },
    metadata: { duration_seconds: 2.1, sample_rate: 24000, mock: false },
  });
  snapshot.artifacts = [
    artifact('anchor'),
    artifact('speech_cue'),
    artifact('music_' + scene.scene_id),
  ];
  snapshot.audio_controls = {};
  snapshot.timeline = {
    schema_version: '1.0.0',
    timeline_id: 'timeline',
    project_id: snapshot.project.project_id,
    duration_seconds: 15,
    video_tracks: [],
    subtitle_tracks: [],
    audio_tracks: ['Production Sound', 'Dialogue', 'Music'].map((name) => ({
      schema_version: '1.0.0',
      track_id: name,
      name,
      cues: [],
    })),
  };
  return snapshot;
}

it('shows pinned voice, canonical text, audio previews and three tracks', () => {
  const snapshot = fixture();
  render(<AudioPanel snapshot={snapshot} />);
  expect(screen.getByText('林恩，不要靠近地面。')).toBeVisible();
  expect(screen.getByText(/Voice Profile v1/)).toBeVisible();
  expect(screen.getByLabelText('播放语音 林恩，不要靠近地面。')).toHaveAttribute('controls');
  expect(screen.getByLabelText('播放语音 林恩，不要靠近地面。').getAttribute('src')).toContain(
    'version=1',
  );
  expect(screen.getByText('Production Sound')).toBeVisible();
  expect(screen.getByText('尚无人工试听结论')).toBeVisible();
});

it('sends typed speech, music and new voice commands without provider parameters', async () => {
  const command = vi.spyOn(api, 'audio').mockResolvedValue({
    project_id: 'p',
    status: 'running',
    schema_version: '1.0.0',
    pause_policy: null,
  });
  render(<AudioPanel snapshot={fixture()} />);
  fireEvent.click(screen.getByText('重新生成这句语音'));
  await waitFor(() =>
    expect(command).toHaveBeenCalledWith(expect.any(String), {
      action: 'regenerate_speech',
      cue_id: 'cue',
      emotion: '克制',
      pace: 'medium',
    }),
  );
  await waitFor(() => expect(screen.getByText('重新设计声音')).toBeEnabled());
  fireEvent.click(screen.getByText('重新设计声音'));
  await waitFor(() =>
    expect(command).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ action: 'redesign_voice', voice_description: '冷静克制' }),
    ),
  );
  command.mockRestore();
});

it('keeps failure visible and prevents edits while running', async () => {
  const snapshot = fixture();
  const command = vi.spyOn(api, 'audio').mockRejectedValue(new Error('Service unavailable'));
  const view = render(<AudioPanel snapshot={snapshot} />);
  fireEvent.click(screen.getByText('生成对白与场景配乐'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Service unavailable');
  view.rerender(<AudioPanel snapshot={{ ...snapshot, status: 'running' }} />);
  expect(screen.getByText('重新生成这句语音')).toBeDisabled();
  command.mockRestore();
});
