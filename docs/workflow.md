# Workflow, Jobs, and Recovery

## Graph contract

`WorkflowGraph` is an explicit validated DAG. Nodes carry execution state plus neutral display metadata; edges carry dependency, condition, success, failure, repair, or human-gate semantics. Graph validation rejects duplicate IDs, dangling references, invalid entry nodes, and cycles.

The standard production graph is created by `build_production_graph()`. The mock Showrunner iterates its topological display order, but execution state lives in the graph contract and checkpoints rather than in an orchestration framework's private state.

## Node lifecycle

Production nodes use pending, ready, running, waiting-human, succeeded, failed, skipped, and cancelled states. Mutations emit typed node progress/completion/failure events. A future adapter may schedule multiple ready subgraphs, provided it preserves dependencies and node events.

## Job lifecycle

Generation work is always a `GenerationJob`:

```text
PENDING → QUEUED → PREPARING → RUNNING → SUCCEEDED
                    ↘ retry PREPARING
                    ↘ FAILED / CANCELLED
```

The complete status vocabulary also includes blocked, evaluating, repairing, and waiting-human so provider work and higher-level production can share one durable contract. `JobManager` enforces allowed transitions and idempotency. Invalid or terminal transitions fail immediately.

## Scheduling rules

The local scheduler is deterministic:

- wait for declared job dependencies;
- sort ready work by priority and creation time;
- acquire weighted logical resources (`LIGHT`, `MEDIUM`, `HEAVY`, `EXCLUSIVE`);
- serialize jobs with the same continuity-chain lock;
- allow independent chains to overlap;
- honor cooperative cancellation;
- retry only retryable provider results while budget remains.

No language model decides resource or concurrency policy.

## Quality and repair route

Shot artifacts pass through deterministic technical QC, a visual/semantic critic, and a cinematic critic. Failed evaluations carry issue type, severity, evidence, and suggested action. `IssueClassifier`, `RepairPolicy`, and `RepairPlanner` distinguish provider/generation defects from shot-design defects.

Within budget, a repair creates a new job, prompt package, and artifact version. Exhausted generation failures request human review; exhausted or inherent shot-design failures route to director replan. Repair never overwrites a prior artifact and never loops without a finite counter.

## Human gates

Story, screenplay, shot-plan, final-cut, and agent-escalation review kinds are represented by `HumanReviewRequest`. A gate changes the workflow node to `WAITING_HUMAN` and persists the unresolved review in the next checkpoint. Resume restores and resolves that same request instead of creating a duplicate.

The mock pipeline auto-approves by default. `--wait-for-human` demonstrates a durable pause.

## Checkpoint and resume

A checkpoint is written after every successful node and when a human pause or failure occurs. It includes graph/project state, completed nodes, all known jobs, artifact records, evaluations, repair plans, retry counts, and human review state.

On restart:

1. load the workspace's latest atomic snapshot;
2. validate it through current contracts;
3. requeue interrupted preparing/running/evaluating/repairing jobs;
4. restore terminal jobs, evaluations, repairs, and reviews;
5. skip succeeded graph nodes;
6. continue from the first incomplete node.

The local JSON store is an implementation of `CheckpointStore`; a database replacement should preserve these semantics.

## Running the mock workflow

```powershell
python scripts/run_mock.py tests/fixtures/sample_brief.json --workspace workspace/demo
```

To prove restart behavior:

```powershell
python scripts/run_mock.py tests/fixtures/sample_brief.json --workspace workspace/resume-demo --stop-after-node storyboard_planning
python scripts/run_mock.py tests/fixtures/sample_brief.json --workspace workspace/resume-demo --resume
```

The run produces typed planning artifacts, six frame placeholders, three first-pass videos, an intentional semantic failure for `shot_002`, a repaired second version, audio/timeline/final placeholders, checkpoints, provenance, and a workflow-completed event.

## Phase 2A execution

`ReasoningMovieProduction` reuses the same graph and downstream media handlers. The first six creative stages call a unified RoleRunner; StoryBible is committed in story_planning and is not overwritten by Visual Director. Screenwriter declares typed entities/scenes, Director creates ScenePlan, and Cinematographer creates one typed ShotPlan per scene.

The executor checks dependencies before starting each node. API pause takes effect at the next node boundary. Successful roles and per-scene cinematography commits are checkpointed; resume bypasses them even if their parent node was interrupted. Validation failures are checkpointed with finite repair counters, while exhausted failures stop the node. The API restores explicit approved reviews and will not bypass a rejected or pending gate.

REST manages commands/snapshots and SSE projects events; details and endpoint paths are in `p2a_runtime.md`. The Phase 1 mock CLI remains runnable without LLM configuration.
