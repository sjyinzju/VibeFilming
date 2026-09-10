# P4D — 真实对白、角色声音与配乐验收

更新：2026-09-09。**远端部署、独立实测和真实粗剪已完成；P4D DoD 尚未全部达到。**
用户已批准技术导出，真实 `final_film` v4 已生成。人工试听结果为**需要修改**：
片头 H3 人声与正式对白重叠，女声音色偏幼态。没有开始 P5。

完整机器可读证据：[p4d_audio_acceptance.json](p4d_audio_acceptance.json)，包含实际
镜像、每个镜像的完整 `pip freeze` / `pip check`、原始实测文件 SHA、各阶段采样、
P4A profiles、所有声音/对白/配乐 SHA、下载验证、容器状态和原始模型代码核对。

## 部署边界与实际镜像

仅向用户授权的 `Developer@106.13.186.155:6081` 上传了八个音频服务/构建文件。
最终 allow-list 包 SHA-256：
`25cf48c22dcc7b11c7b8d3a3e19075df2fd148ff8c8efc037bf69038cee4a493`。
没有上传 `.git`、workspace、媒体、`.env`、凭据、无关源码或模型权重。
依赖全部安装在独立 Docker image 内，没有修改 host Python、原有 ComfyUI/vLLM/
FLUX 环境、驱动或 CUDA。没有下载、移动或修改用户模型权重。

| 本地构建 tag | 实际 Docker image ID |
|---|---|
| `movie-agent-audio-runtime:p4d` | `sha256:e0d2186e9d832c0715a3ec76b20c652a0a1c08e4d9c1d6363b54e4f26bc7998d` |
| `movie-agent-tts:p4d` | `sha256:ba45190f49016fb456dbe278bea1680ce440a87a1af8c50b9deda5540f9326b5` |
| `movie-agent-music:p4d` | `sha256:1df7161cefefe798256098ae50de08c7f992234a11099676de5a7c07b4b2d13d` |

三者均为 Linux ARM64、本地构建，`RepoDigests=[]`，没有 registry digest。
基础镜像为固定 digest 的 Ubuntu 24.04；实际 Python **3.12.3**、Torch/TorchAudio
**2.10.0+cu130**、TorchVision **0.25.0+cu130**、CUDA **13.0**。
Qwen-TTS **0.1.1**、transformers **4.57.3**、accelerate **1.12.0**；
Music 使用 diffusers **0.37.0** 和 ACE-Step **1.5.0**，官方源码固定在
`ca1e85fe9430179831e6bc6be790c332190a3866`。均使用 SDPA，没有额外安装 FlashAttention。
Torch/NVIDIA 使用官方 indexes，普通已声明依赖在确认 PyPI CDN 超时后使用清华镜像。
直接依赖已固定；本次完整传递依赖已记录，但 requirements 尚不是带 hash 的全量 lock。

`pip check` **不是全绿**：Qwen/ACE 包声明的 Gradio 及部分 UI、训练、下载、可选 LM/
量化依赖未安装；NVIDIA cuSPARSELt 0.8.0 wheel 的 `sbsa` 平台标记被 pip 报不支持。
实际 cuSPARSELt 动态库加载、GB10 CUDA 矩阵运算、服务 import 和两种模型推理均通过。
因此这里只验收当前 API 推理配置，不声称上游包所有可选功能都可用。

## 健康接口与故障定位

- TTS `127.0.0.1:8002/health` 实测 `ready=true, offline=true, attention=sdpa`；
  `/v1/models` 返回 VoiceDesign 和 Base。两个生成接口返回 binary `audio/wav`。
- Music `127.0.0.1:8003/health` 实测 `status=ok, models_initialized=true`；
  `/v1/models` 是官方兼容列表。P4A 用 `/v1/model_inventory` 核实
  `acestep-v15-turbo.is_loaded=true`，可选 LM 未加载。
- 独立测试及生产后均验证 TTL 正常停止。17:44 中国标准时间复查，两者已停止、
  `ExitCode=0, OOMKilled=false`。**停止状态下端口不会持续返回健康响应**；下一次音频
  job 通过 P4A `ensure_ready` 启动。默认生产 warm TTL 为 300 s。

真实失败及修复保留在 `workspace/p4d-build` 日志和旧的自建失败容器中：

1. 公共 PyPI CDN 下载超时：先对同一文件测速，确认吞吐问题，再切换普通依赖镜像。
2. ACE 官方启动同步 checkpoint 中的 Python 代码，直接 RO mount 导致只读文件系统错误。
   launcher 只复制 `.py` 到独立 writable overlay；JSON、tokenizer、权重仍链接原 RO
   mount。官方模型实现未打补丁，原路径两份 Python 代码 SHA 复核未变。
3. 最初 overlay 把大 tokenizer JSON 当小元数据，以及官方 preflight 要求现存可选 LM
   目录，分别导致启动前失败。修为仅复制 Python，并链接用户已有 LM 目录；LM 仍不加载。
   缺失本地 checkpoint 直接失败，下载 hook 不允许取新权重。
4. P4A 原启动轮询没有在非 OOM 容器退出时立即停止；现已 fail-fast，保留真实退出错误。
5. Studio 原按 artifact ID 混入旧版本 QC；现按版本过滤，新粗剪不继承旧 Mock/历史结论。

## 独立 P4A telemetry 与 profiles

以下为单服务、GB10 unified-memory 的**冷基线可用内存减去当前可用内存**。
resident 取最后一次推理后压力，peak 是采样值，不能当作精确 allocator 峰值。

| 指标 | TTS | Music |
|---|---:|---:|
| cold 可用 | 117.7806 GiB | 117.7872 GiB |
| startup → ready | 63.154 s | 33.695 s |
| ready 可用 | 108.6863 GiB | 107.5927 GiB |
| startup 采样峰值压力 | 11.9348 GiB | 9.8166 GiB |
| inference 采样峰值压力 | 10.4656 GiB | 10.5785 GiB |
| after inference 可用 | 107.3201 GiB | 107.2701 GiB |
| resident 实测压力 | 10.4606 GiB | 10.5171 GiB |
| 全过程采样峰值 | 11.9348 GiB | 10.5785 GiB |
| TTL 停止后可用 | 117.7857 GiB | 117.7748 GiB |
| P4A resident / peak budget | 11 / 16 GiB | 11 / 15 GiB |
| P4A startup cost | 64 s | 34 s |

先完成实测，再填写 `registry.py` 与 `.env.example`，没有编辑用户 `.env`。
预算为 resident 向上取整、实测全过程 peak 向上取整后加 4 GiB，另有 P4A 12 GiB
headroom。`estimate_basis` 记录日期、镜像、工作负载和采样限制。每服务并发 1；
`tts → movie-agent-tts`、`music → movie-agent-music` 为明确 allow-list，禁止任意容器。
显式设为 0 的未知预算仍在 eviction 前 fail closed。更长台词/音乐和可选 LM 未标定。

TTS VoiceDesign/Base 模型 load 分别 **29.076 / 29.185 s**。
独立 VoiceDesign：1.92 s 音频，耗时 3.690 s；同一 anchor 的两句中文
“林恩，不要靠近地面。”、“留在舱内，等我回来。”分别耗时 **3.366 / 2.942 s**，
音频 2.48 / 2.24 s。英文句耗时 **3.096 s**，音频 2.56 s。均为 24 kHz mono PCM16。
独立 instrumental Music：**15.00 s**、48 kHz stereo PCM16，耗时 **7.224 s**。
WAV 可解码、不静音、无削波。用户已试听全片并提出下述两项问题；独立 smoke 的
“可理解、音色稳定、无人声、音乐品质”没有单独人工通过结论。

原始实测：[TTS](../workspace/p4d-smoke-20260909/tts-acceptance.json)、
[Music](../workspace/p4d-smoke-20260909/music-acceptance.json)，失败尝试另行保留。

## 真实项目产物

已执行 `.\.venv312\Scripts\python.exe -m scripts.accept_audio --run-real`。
原 P4C 项目复制到 `workspace/p4d-audio-acceptance`，原 workspace 文件 hash 未变。
已成功的音频在 checkpoint/重启后复用，所有 Qwen reasoning / FLUX / H3 / VLM
调用均被 acceptance guard 禁止，实际重复上游推理 **0**。

以下均为真实音频、`mock=false`；文件在
`workspace/p4d-audio-acceptance/project_3effeb45f3844bf89c2c96e83770114b/artifacts/media/<artifact_id>/v1.wav`。

| Artifact | 音频时长 | 本次生成耗时 | SHA-256 |
|---|---:|---:|---|
| `music_SCENE_01` v1 | 15.00 s | 6.998 s | `e0def85136ec37a69a89827cae91418c79b8e6ca8f9d8cf508e335f4d95a959b` |
| `music_SCENE_02` v1 | 15.00 s | 5.894 s | `ea4e3ec268f584fa1d55e78566fcce28656e0361077edfde676322effe0c2596` |
| `voice_anchor_CHAR_ELLA_001` v1 | 4.08 s | 5.441 s | `6350c380ccc89c25940638a27666eb4513df101c69b5920cadc81fe2ff1740be` |
| `speech_dialogue_SCENE_02-SHOT-01_2` v1 | 4.40 s | 5.139 s | `d005426611183aedf8cc57dce0ee40f4803e0502c28ddd59620fbeccf2bc2836` |
| `voice_anchor_CHAR_LYNNE_001` v1 | 2.96 s | 3.465 s | `80c93a1b34e81bc29243a270c519a000e3aad360e6f19d2fe412df362894bec3` |
| `speech_dialogue_SCENE_01-SHOT-01_1` v1 | 2.56 s | 3.046 s | `fa1f63f0777139b01d26d7cd46f0ee8cf08e1dcbfd3db8fd093567c7d699eeb8` |
| `speech_dialogue_SCENE_02-SHOT-01_1` v1 | 2.88 s | 3.455 s | `79d735dc4a30c111214329a10f240b898576727cad69b9a1964a125d493e78ea` |

两份 `voice_profile_CHAR_*` v1 固定对应 anchor/version/SHA/精确 transcript；
林恩两句跨 Scene/Shot 复用同一 profile，未逐句重做 VoiceDesign。
舞台提示“低声自语/惊恐/冷静”保留为 prosody，不被读成台词。

| 正式对白 | 全片实际播放时间 |
|---|---|
| 林恩：下面……到底是什么样子？ | 0.400–2.960 s |
| 林恩：别碰我！你会害死我的！ | 15.400–18.280 s |
| 艾拉：地面不会咬人，悬浮者。你的装置坏了，下来吧。 | 20.424–24.824 s |

真实 [rough_cut v4](../workspace/p4d-audio-acceptance/rough_cut.mp4)：30.00 s、
1920×1080、24 fps / 720 帧、H.264 yuv420p + AAC 48 kHz stereo，12,424,821 bytes。
SHA-256：`e839429fc4ba7ed470c9f374f22af93885176c77f64586b70642103b2607ab63`。
技术 QC 通过，A/V end drift 0；解码后 -16.20 LUFS / -1.32 dBTP。
AAC 编码后 true peak 略高于 -1.5 dBTP 配置目标，没有削波，未声称精确达到该目标。

已通过现有 HTTP download API 下载并核对完整 SHA 和长度：
[SRT](../workspace/p4d-audio-acceptance/dialogue.srt)、
[Dialogue stem](../workspace/p4d-audio-acceptance/dialogue_stem.wav)、
[Music stem](../workspace/p4d-audio-acceptance/music_stem.wav)、
[Native sound stem](../workspace/p4d-audio-acceptance/native_sound_stem.wav)。
媒体、3 stems 和 SRT 都已有 immutable artifact/version/hash，完整清单见 JSON。

**真实 [final_film v4](../workspace/p4d-audio-acceptance/final_film.mp4) 已导出。**
用户明确“批准导出”后，按原版本/SHA 批准
`review_e54cd6ca463c4858b61fe006ce506574`，继续既有 `final_render`。
本地 FFmpeg 导出耗时 **32.235 s**；项目状态 `completed`，技术 QC 全部通过，
`mock=false`。文件为 30 s / 1920×1080 / 24 fps / H.264 + AAC / 48 kHz stereo，
12,424,821 bytes、A/V end drift 0。原 P4C final v3 保留。

Final v4 SHA-256：
`e839429fc4ba7ed470c9f374f22af93885176c77f64586b70642103b2607ab63`。
它与批准的 rough v4 **字节完全相同**，确实保留用户试听过的版本。
manifest 为 `final_film_manifest_v4`；MP4、SRT、3 stems 的精确版本下载及
`/projects/{project_id}/exports/final` 均通过完整 SHA 验证。
本次导出没有新增音频或上游 AI 推理；所有既有 voice/speech/music 版本与 SHA、
非导出节点状态/完成时间、原始 P4C workspace 均核对未变。

用户试听原文：“视频开头出现了部分minimaxH3的配音和后期配音重叠的问题，然后女声有点过于幼态”。
已通过现有音频命令保存 `audio_listening_result`，`passed=false`，精确绑定
final v4 SHA 和三句 speech v1 SHA。Gate 批准表示技术导出授权，**不代表声音质量通过**。
两项问题在本版中仍存在；没有未经用户确认把“偏幼态”当作已修复。
现有 native duck 为 -16 dB，只降低声音而非去除 H3 人声，也不会消除 TTS 区间外的原配音。
声音修订应在后续版本处理受污染原声区间与艾拉的 voice design；本次先保留已批准版本。
新增导出和试听证据见 [final-export.json](../workspace/p4d-audio-acceptance/final-export.json)。

Studio：`http://127.0.0.1:5177/?project=project_3effeb45f3844bf89c2c96e83770114b`，
guarded API：8087。真实浏览器确认粗剪完整加载 30 s、播放时间推进到 15.59 s；
首句真实对白完整播放 2.56 s、readyState=4、无媒体错误；
角色声音、3 句对白、2 段配乐的预览均绑定精确版本。配乐面板显示风格与场景意图，
完整 provider prompt 仍保存在 immutable provenance，避免长 JSON 占满试听页面。

## Architecture and contracts

The existing AudioProvider / MediaRuntime / Artifact / Timeline boundaries remain
the authority. `Qwen3TTSAudioProvider` serves speech; `AceStepMusicProvider` serves
scene music. `MOVIE_AGENT_AUDIO_PROVIDER=real` binds both and removes MockAudio from
the registry. There is no fallback to Mock when a real provider fails.

`CharacterVoiceProfile` stores identity, revision, project/character, design intent,
language, exact anchor artifact ID/version/SHA, reference transcript, provider/model
and creation time. It is a versioned structured Artifact outside StoryBible facts.
VoiceDesign produces one immutable anchor per voice revision. Base clone reuses
those exact bytes and transcript for every cue. Provenance links the profile to
its anchor; speech stores the exact profile and anchor reference.

`DialogueCue` stores project/scene/shot/character, exact canonical text, profile
ID/version, shot-relative start/end, language and performance intent. Extraction
uses committed shot dialogue, then performance dialogue or an unambiguous
single-shot screenplay assignment. Unknown/duplicate speaker labels and unassigned
multi-shot screenplay dialogue enter HUMAN_REVIEW. No LLM rewrites dialogue.

The ready-job DAG gains `audio_prepare`, character voice jobs, cue speech jobs and
scene music jobs. Their dependency is committed shot planning, not a hardcoded
H3→TTS→Music chain. Existing post nodes depend on completed audio jobs. Audio-only
revision commands require completed upstream nodes and invalidate only relevant
audio and post nodes. Voice revision changes its character's speech; music gain or
enable changes only the mix. Output fingerprints and durable task journals preserve
completed work. Unknown ACE submission outcomes fail closed for operator review.

TTS responses stream binary WAV (bounded to 128 MiB); cloning sends only metadata
and the SHA of an anchor already retained on Spark, with no media upload or Base64
JSON. Anchor SHA/ownership is checked before dispatch, and the server verifies
the exact transcript. ACE async task
IDs are journaled before polling and reused after restart. Downloads are restricted
to the configured service's `/v1/audio` path.

## Timing, mixing and limitations

Defaults: pre-roll 0.4 s, post-roll 0.4 s, minimum gap 0.25 s. Slots are allocated
deterministically from shot duration and text length estimates. Actual WAV duration
is measured after generation. A short line retains silence in its slot. An overlong
line enters timing review; speech is neither truncated nor aggressively stretched.
There is currently no automatic pace retry. Timing is cue-level, not word-level
alignment or lip sync. Explicit overlapping dialogue is not implemented and is
rejected for timing repair.

**Official Qwen Base cloning does not expose native emotion/pace instruction
control.** Current UI values are recorded performance intent and regeneration
creates a new sample; those values are not applied as native model controls. UI,
service health and artifact metadata disclose this. Actual emotion/pace control
remains an unmet part of the requested user controls. It must not be presented as
completed merely because the fields and buttons exist.

H3 native audio retains GENERATED_NATIVE_AUDIO but represents production sound,
ambience and noncanonical voices. The video compiler adds speech-suppression intent
when canonical dialogue exists, without changing Shot IR. Existing H3 clips are
reused unchanged: ducking cannot remove old unintelligible voices completely.

Three named tracks feed the existing FFmpegPostProcessor: Production Sound,
Dialogue, Music. Native sound ducks by -16 dB and music by -6 dB around actual speech,
with 0.12 s attack and 0.35 s release. Base music gain is -14 dB; intensity changes
the mix gain. All values are settings. Float PCM stems preserve headroom before
whole-film two-pass loudnorm; final remains H.264 / AAC / 48 kHz stereo.

Canonical cue text and measured placement produce immutable SRT, default sidecar,
optional burn-in. Post-only reorder preserves separate tracks and source offsets;
re-enabling subtitles uses canonical cues. Final metadata and manifest reference
native/dialogue/music stems and SRT, using existing exact-version downloads.

Deterministic QC checks readable PCM16, duration, rate/channels, nonsilence and
clipping. It does not prove intelligibility, timbre consistency or absence of
vocals. Listening results require explicit notes and are pinned to exact final and
speech versions. Historical listening records do not approve a new revision.

## 验证与剩余 DoD

- Backend 全量：**330 passed / 4 opt-in skipped**，90.92 s；随后版本 QC 修复的
  定向回归 **4 passed**。P4D 共 14 项、资源运行时共 32 项包含在已执行检查中。
- Frontend 48 项、离线浏览器 11 项此前通过；最后音频面板显示调整再跑 **3 passed**，
  TypeScript + Vite build 再次通过。现有约 572 kB bundle 提示保留。
- 真实浏览器验证 Gate 媒体播放与版本证据。随后依据用户明确批准提交精确版本 Gate，
  通过应用命令记录用户“需要修改”的试听结果，没有自动标记通过。
- 确定性 FFmpeg 回归覆盖三轨、ducking、字幕 sidecar/burn-in、精确下载与无重复生成。
- 普通 CI 不访问 Spark；真实校准与生产均显式 opt-in、没有 Mock fallback。
- 最终 `git diff --check` 通过；既有未提交 P4C 工作保持原状，没有提交或推送。

### 请求的 27 项汇报

| # | 项目 | 结果 |
|---|---|---|
| 1 | TTS architecture | 独立 FastAPI Docker，VoiceDesign + Base，SDPA，binary WAV，SHA anchor 复用 |
| 2 | Music architecture | 独立官方 ACE API，固定源码，RO 权重 + writable Python overlay，LM 关闭 |
| 3 | 实际镜像 | 三个真实 image IDs 及完整依赖清单见上文/JSON；无 registry digest |
| 4 | 8002/8003 | 运行时健康与模型加载通过；当前完成 TTL 回收，按需启动 |
| 5 | VoiceProfile | canonical voice identity，版本化 anchor SHA 与 provenance 完整 |
| 6 | DialogueCue | 确定性 canonical text、speaker、profile version、cue timing |
| 7 | Voice anchor | 2 个真实角色 anchor + 独立 smoke anchor |
| 8 | 两句真实 TTS | 同一林恩 profile 两句 + 艾拉一句，另有中文/英文 smoke |
| 9 | Voice consistency | 共享精确 anchor 已验证；用户认为女声偏幼态，尚无整体声音通过结论 |
| 10 | TTS latency | 独立 clone 2.942–3.366 s；生产 speech 3.046–5.139 s |
| 11 | TTS memory | resident 10.4606 / 全程采样峰 11.9348 GiB |
| 12 | Music Artifact | 两场景各 15 s 真实 instrumental 请求 + 15 s smoke |
| 13 | Music latency | 独立 7.224 s；生产 6.998 / 5.894 s |
| 14 | Music memory | resident 10.5171 / 采样峰 10.5785 GiB |
| 15 | P4A profiles | tts 11/16 GiB，music 11/15 GiB，实测 basis，warm TTL 300 s |
| 16 | Dialogue timing | canonical cue + 实际音频时长，无重叠；过长进入 review |
| 17 | Native ducking | -16 dB，attack 0.12 s / release 0.35 s，可配置 |
| 18 | Music ducking | -6 dB，基础 gain -14 dB，可配置 |
| 19 | Final mix | 真实三轨 final_film v4；30 s、零 A/V end drift，技术 QC 通过 |
| 20 | Subtitle | 3 条 canonical SRT，immutable 并经 HTTP 下载核验 |
| 21 | final_film version/hash | final v4 已生成，SHA 与已批准 rough v4 一致；原 P4C v3 保留 |
| 22 | Human listening | 需要修改：片头 H3/TTS 人声重叠、女声偏幼态；结果 pinned 到 final v4 |
| 23 | Web UI | Voice、对白、音乐、三轨、试听、版本化重新生成和下载；真实粗剪播放验证 |
| 24 | Tests | 全量 backend 330/4 skipped，定向修复 4；frontend 48 + 定向 3；browser 11 + 真实播放；build 通过 |
| 25 | OOM | 本轮 P4D 无 OOM；最终两容器 TTL 正常退出 0；初期启动故障不是 OOM |
| 26 | 重复上游 AI | 0，guard 禁止调用且四个容器 ID/State 未变 |
| 27 | P4D DoD | 尚未全部满足；未开始 P5 |

剩余项目：片头 H3 与正式对白重叠、女声偏幼态的修订及再次人工试听；
独立清晰度/音色稳定/无人声与配乐品质尚无全部通过结论。
官方 Qwen Base 不提供原生 emotion/pace 控制，当前只记录
表演意图；自动轻微时长 fit 重试和明确 overlapping speech 尚未实现。
当前实际对白均已放入时槽，无需为本次 acceptance 重生成。更长工作负载资源未标定。
没有 ASR、逐词对齐或 lip-sync 的准确性声明。完整 P4D DoD 暂不能标记通过。
