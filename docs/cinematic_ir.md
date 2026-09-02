# Cinematic Contract / IR

## Purpose

Cinematic IR is the stable language between creative planning, deterministic production, prompt compilation, providers, quality control, and future frontends. It describes filmmaking intent and state, never a particular model's prompt syntax or API fields.

Every public contract inherits `ContractModel`, rejects unknown fields, contains `schema_version`, round-trips through JSON, and exposes Pydantic `model_json_schema()`. Contract migrations can therefore dispatch on the serialized schema version before validation.

## Aggregate structure

`Project` is the aggregate root. It contains the frontend-ready `ProjectBrief`, approved creative direction, story and visual bibles, characters, locations, props, scenes, ordered shots, continuity chains, and three distinct memory/state records.

`ProjectBrief` separates hard user intent from discretionary creative interpretation:

- `user_constraints` and `must_preserve` are non-negotiable.
- `creative_freedom` declares how much unfilled space the creative roles may explore.
- basic, story, character, world, visual, cinematography, audio, and production fields can map directly to a future input form.

`CreativeDirection.preserved_user_constraints` makes the handoff from brief to expansion explicit.

## Shot is the production core

`Shot` contains:

| Concern | Typed fields |
|---|---|
| Identity | `shot_id`, `scene_id` |
| Narrative | `ShotNarrative.purpose`, `beat`, action, dialogue |
| Timing | `duration_seconds` |
| Camera | `CameraSpec`, `CameraMotion`, `CompositionSpec` |
| Performance | `PerformanceSpec` list with action/emotional boundaries |
| Visual | lighting, requirements, reference artifact IDs |
| Frame boundary | `FrameAnchors.first_frame`, `last_frame` |
| Continuity | chain, previous/next shot, before/expected-after state |
| Generation | provider-neutral strategy, retry budget, quality profile |

Provider prompts, endpoints, checkpoints, and model-specific options do not belong in `Shot`. `PromptCompiler` creates a separate `PromptPackage` only at the execution boundary.

## Continuity state

`ContinuityState` stores typed maps of `CharacterState`, `PropState`, and `LocationState`, plus global scene, lighting, weather, damage, previous-shot, and last-frame information.

The deterministic transition functions are:

```python
issues = validate_transition(previous_state, shot)
next_state = apply_shot_effects(previous_state, shot)
validated_next_state = derive_next_state(previous_state, shot)
```

Only explicitly declared end-state fields are merged. Nested contracts remain typed during partial updates. A conflict such as broken prop followed by a required intact prop yields a structured `EvaluationIssue` of type `continuity_conflict` before generation.

## Frame anchors

`FrameAnchor.kind` supports none, generated, previous-shot last frame, and external reference. Combining the independently typed first and last anchors represents first-only, last-only, first-plus-last, or no-anchor strategies.

The current rule-based frame planner does not generate pixels. It links the last-frame artifact of a shot to the next shot's first-frame plan and attaches the planned boundary state. Future image providers materialize the same artifact IDs without changing downstream contracts.

## Strategy and prompt boundaries

`GenerationStrategyPlanner` chooses among text-to-video, image-to-video, first-frame, first-plus-last-frame, reference-to-video, edit-then-video, extend, video-to-video, split-shot, and static-plus-post-camera according to planned anchors and advertised capabilities.

`GenericPromptCompiler` demonstrates IR-to-prompt compilation for tests. It produces named narrative, camera, performance, lighting, and continuity sections without mutating the shot. Real provider compilers can later be registered beside it; they should not add fields to Cinematic IR.

## Artifacts and provenance

IR objects reference material by artifact ID, never by filesystem path. `Artifact` identifies one immutable version and provides its URI only at the registry boundary. Its provenance can reconstruct the role/tool/provider/prompt/strategy/input/repair path that produced it.
