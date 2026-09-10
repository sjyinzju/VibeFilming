# P5 Hero Film 实际验收（2026-09-10）

**P5 未达到整体 DoD：没有 ≥75 秒 accepted candidate，没有 Hero Film MP4。**
质量机制和真实素材链路已实现并做了分层验证，但不能替代作品验收。
项目停于 `waiting_human / REFERENCE_QUALITY_BUDGET_EXHAUSTED`，没有批准任何人工门禁。
全部空闲模型已由 P4A 停止，活动租约为0。没有启动P6。

- [Morning Review：参考图各版本与实际音频](http://127.0.0.1:8099/morning-review.html)
- [实际项目 Studio](http://127.0.0.1:5189/?project=project_58ad9203c92a4e4fb8c3fe18b8d1a839)
- 完整机器记录：`workspace/p5-hero-film/acceptance.json`
- 下载/浏览器验证：`workspace/p5-hero-film/review-verification.json`
- 技术架构与限制：[p5_quality.md](p5_quality.md)

本地链接依赖8089 API、5189 Studio、8099报告服务器；没有继续推理。素材与报告本身已保存。

## A. 平台：1–10

| 项目 | 实现与实测边界 |
| --- | --- |
| 1 质量架构 | 在现有 Evaluation / VisionInspection 上聚合 Shot、Scene、FilmQualityReport；SHOWCASE视觉最低.80、cinematic最低.75，精确匹配Artifact/version/SHA。未知音频维度保持unknown/human_required。 |
| 2 参考架构 | ReferenceIdentitySet存储角色/地点/道具/风格角色，selected绑定通过检查；Resolver按镜头解析确定版本/SHA，缺必需参考则阻断。角色多个参考角色暂复用同一全身图，尚未验证多视角身份稳定。 |
| 3 Kontext runtime | 用户已有NVFP4单文件、现有Comfy loader与CLIP-L/T5/AE；独立movie-agent-kontext、API9002、P4A生命周期管理。实际完成光照smoke1次和Hero参考编辑4次；未下载。仅单源image_edit，无多图联合条件、mask、inpaint/outpaint。 |
| 4 Kontext memory | smoke启动9.296s、推理88.447s；采样统一内存压力30.161GiB，推理后24.522GiB，无smoke OOM。配置驻留25GiB/峰值35GiB，另留12GiB系统/安全余量，全部属于GB10同一个内存池。 |
| 5 Frame gate | 选定参考→首尾帧→真实检查→H3；版本/SHA精确通过才允许视频。Hero在更早的参考门禁被阻断，首尾帧/H3没有真实执行。 |
| 6 Cinematic critic | 真实Qwen使用VLM观察、规范上下文和合法evidence ID评分；修复propertyNames schema不兼容。真实运行于隔离旧片baseline，Hero无视频可评。 |
| 7 Film critic | 使用实际timeline/scene/shot摘要与采样证据的film proxy；真实baseline已运行。不是逐帧完整观影，未对不存在的Hero Film评分。 |
| 8 定向修复 | 按画面、声音、音乐、后期问题路由，成本是相对工作单位。Hero实际frame_edit4次，关键对象/兜帽修改失败；视频修复路径单测覆盖，真实H3修复0。 |
| 9 计算预算 | H3≤18、每镜头最多1次修复；Kontext≤2/图像链；TTS≤2/cue修订；music最多1次/场景修复。实际更保守地限制VLM≤3/Artifact ID（跨版本）。持久预留、精确缓存复用、重启不清空失败预算。 |
| 10 UI | 现有Inspector增加Quality分数、缺失证据、参考版本、比较与历史；修复无视频报告时0/0显示，实际项目显示0/12。Morning Review提供参考原图/编辑版和真实音频。 |

25项平台机制有对应实现、测试及部分实测；**完整Hero首尾帧→H3→视频修复→全片评审→最终导出没有走通，不能宣布平台发布验收全部完成。**

## B. Hero Film：11–33

| 指标 | 实际结果 |
| --- | --- |
| 11 Project ID | project_58ad9203c92a4e4fb8c3fe18b8d1a839，独立于旧P4B/P4C/P4D项目 |
| 12 Runtime | 计划105s；accepted **0s**；候选片时长不存在 |
| 13 Scenes | 3个，均已canonical commit |
| 14 Shots | 12个，5–12s/镜头，每场4个 |
| 15 accepted/rejected/human-review | 0 accepted；0个已生成且判废视频；12个计划镜头因参考阻断进入人工处理记录。未生成镜头不算已评视频。 |
| 16 故事 | 《悬浮症》：林恩悬浮装置故障，在城市与地面交界遇见艾拉，从害怕重力到接触地面。三场为故障/相遇/接纳。这是提交计划，不代表已形成可观看叙事。 |
| 17 FLUX | 6次成功参考图生成；另有1次启动OOM，在图像推理提交前失败 |
| 18 Kontext | Hero4次真实编辑；平台smoke1次单列 |
| 19 H3 | 0次dispatch，0个视频 |
| 20 H3 repairs | 0 |
| 21 VLM | Hero18个inspection revision，6个参考各3轮；一次revision内部最多2次语义验证尝试，**18不是HTTP请求总数**。平台smoke3轮和baseline2个视频检查不混入Hero预算。 |
| 22 TTS | 5个真实任务：2个VoiceDesign声音锚点、3段canonical台词；未声称试听通过 |
| 23 Music | 3个真实场景音乐，每段35s；自动修复0 |
| 24 Model switches | 相邻已完成项目资源观察的service变化6次；不是并存模型驻留变化。P4A启动状态转换13次：Qwen4、FLUX2（含失败启动）、VLM4、music1、TTS1、Kontext1。 |
| 25 Peak memory | Hero采样统一内存压力峰值 **88.069GiB**，总统一内存121.695GiB；不是CUDA allocator精确峰值，不能与RAM重复相加。 |
| 26 OOM | 1次确认的FLUX启动OOM，错误码已保存，未产生受损媒体。未观察到后续OOM。 |
| 27 Wall time | Hero创建至最终停机记录7395.525s，约2小时3分，含诊断/等待/恢复；不含此前baseline/smoke。资源观察execution累计2948.876s，warmup累计1499.446s，不等于全任务墙钟。 |
| 28 Quality scores | Hero影片/视频得分不存在；6个参考仅Ella通过。参考分数见下表，不能冒充影片分数。 |
| 29 Unresolved | Lynne兜帽/服装目标未实现；Fall区飞碟和Style人物未移除；Sky检查证据无效；Prop亮度判定可能过严；角色脸部信息有限；TTS/音乐未人工试听。 |
| 30 Final Artifact/version/SHA | **均不存在**，technical_candidate_final=null |
| 31 Download URL | 无最终MP4 URL。报告页18个真实媒体版本可下载，SHA已验证。 |
| 32 SRT/stems | 无final SRT、混音stems或Render Manifest。已有3段独立TTS和3段独立场景音乐，不能称为成片混音。 |
| 33 mock=false | 10个图像版本与8个音频版本的metadata及provider provenance均mock=false；18次下载内容SHA与Artifact一致。音频Range返回206/1024字节，页面6段音频metadata可解码；不证明可懂度或审美已通过。 |

资源来源：workspace/_resource_runtime/spark-state.history.jsonl，按project/job归属过滤，使用完整历史而非滚动100条。
最终清理记录：workspace/p5-hero-film/final-cleanup.json。

### 最终参考检查

| 参考 | 图像版本 | image quality / artifact detection / prompt alignment | 最终证据 |
| --- | --- | --- | --- |
| Lynne | v2 | .45 / .95 / .30 | human_review。仍戴兜帽；VLM还报告颜色/袖口光源/半透明外层不符，并重复列出兜帽缺陷。不是已校准的客观量表。 |
| Ella | v1 | 1.00 / 1.00 / 1.00 | pass，唯一selected主体。只是初始身份参考通过，不证明跨镜头一致性。 |
| Sky city | v2 | 无有效最终分数 | 两次有终止响应的提案均缺少major/critical问题所需可观察证据，Core拒绝提交；第三轮预算已消耗。 |
| Fall zone | v2 | .95 / .98 / .00 | human_review；飞行圆盘未移除，人工视觉检查也看见仍在。 |
| Suspender | v1 | 1.00 / 1.00 / .00 | human_review；VLM把蓝光不够柔和判为critical，存在过度阻断风险，需人工校准。 |
| Style | v2 | .95 / .98 / .00 | human_review；evidence指出环境图仍有人物，未形成有效细分issue，Core保持不确定性；人工视觉检查也看见人物仍在。 |

前两轮审核存在具体工程问题：单主体图混入全局其他人物/地点要求；Thinking VLM未配置reasoning parser。
已修正范围，将旧停止容器保留为movie-agent-vlm-pre-p5-reasoning，同镜像/模型挂载的替代容器加入
--reasoning-parser qwen3。第三轮回包有独立reasoning，Ella原图此前被编造的“必须印角色名”要求消失。
这支持该控制图的改善，不能推断其他判断都可靠。未进行第四轮，未通过重命名绕过预算。

四次Kontext有真实像素变化和source→output provenance，但降低兜帽、移除圆盘/人物等关键请求失败。
不能用部署成功、文件生成成功或光照smoke成功宣称定向一致性修复成功。

### 音频与旧片对比

canonical台词只有：林恩“稳住……别慌。”、艾拉“地心引力，是拥抱。”、林恩“原来……不疼。”。
对应TTS为1.52s、3.36s、0.80s。角色ID误入dialogue字段和“无”占位对白已依据既有剧本/表演文本修复，
没有另写替代台词。Ella声音设计采用成年、低声区、克制语气revision2，音色与语速仍需听。

隔离旧P4D baseline为两镜头30s。真实复评的视觉/cinematic分别.65/.25、.00/.00，均human_review，accepted0。
baseline使用更早critic配置，不能与新参考分数直接相减宣称质量提升。
旧P4D目录每次运行前后均比较文件hash、保持不变；旧验收项目/成片和原有工作区改动均保留。

## C. 效率：34–37

| 指标 | 实际结果 |
| --- | --- |
| 34 generations per accepted shot | 未定义，分母0；不填0制造效率成功。 |
| 35 repairs by type | Hero frame_edit4、video repair0、music repair0、自动TTS修复0；声音设计为明确revision2初次生成。平台光照smoke1另计。 |
| 36 avoided H3 regenerations | 12个计划镜头被前置门禁阻断，H3 dispatch保持0。反事实“避免多少重生成”不可测，不填12次省下的重生成。 |
| 37 checkpoint/resume | 多次恢复保留canonical镜头、6个FLUX图、4个编辑及音频；成功精确输入/配置结果复用，账本保持18次VLM预留。单参考异常不再跳过独立资产，耗尽预算在GPU租约前阻断。 |

FLUX启动根因有直接日志：Qwen仍驻留、物理空闲统一内存不足，allocator只获6.81GiB，加载失败返回503/
state=failed/RESOURCE_EXHAUSTED。P4A现识别终止失败，不无限等待loading；冷启动检查同池物理空闲48GiB
（40GiB allocator+8GiB reserve），必要时驱逐空闲Qwen。随后6张图生成成功，失败记录未删除。
最后一次运行在空视频timeline进入audio_post时抛ValueError；现改为显式技术人工review并保存checkpoint，
当前项目已应用该修复，未伪造通过或再次生成媒体。

## D. 验证：38–41

| 范围 | 实际检查 |
| --- | --- |
| 38 Backend | 最近完整套件 **357 passed / 4 skipped**（92.98s）；skip为真实服务opt-in。Windows仅当前进程策略Bypass，无全局修改。之后参考失败隔离/预算预检相关19 passed，最新独立3测试（含空timeline人工门禁）全部通过；未将新增测试计入此前全套357。 |
| 39 Frontend | 完整单测49 passed；之后质量分母修正的定向单测2 passed。未声称重跑完整50套件。 |
| 40 Browser | 既有Studio回归9 passed；新增Quality回归passed；post/audio各1 passed。实际Hero复核10图、6音频metadata、18个exact下载hash、音频Range206，page errors为空，截图保存；无MP4可做长片播放验证。 |
| 41 Build | 最新TypeScript+Vite build通过；577.71kB JS仍有>500kB chunk warning。目标Python compileall通过，git diff --check无错误（有CRLF提示）。 |

## E. 状态：42–44

**42 — 未达到DoD。** 核心目标是可观看的≥75秒作品，本轮未产出。真实长片H3、视频质量/修复、声音混音与最终导出均未在本项目验证。

**43 — 人工待确认：** 参考目标是否合理、Prop亮度严重度是否过严、Lynne可辨认脸部/服装基准、地点/风格图残留对象，
以及TTS/音乐音色、可懂度、语速、旋律关系。没有电影可做整片审美批准，这些检查未由AI代签。

**44 — 暂无证据证明现在必须新增LoRA/lip-sync/其他模型。** 已确认阻断是参考目标/编辑可靠性与critic校准。
不能从未进入视频阶段推断一定要新模型。规划已减少正面长对白，但尚未实片验证；无ASR或口型同步能力。
本轮未下载这些模型，未继续P6。
