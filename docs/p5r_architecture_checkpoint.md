# P5R architecture checkpoint — 2026-09-10

Historical checkpoint. The newer platform-first A–G audit and current implementation status are in [p5_platform_checkpoint.md](p5_platform_checkpoint.md); statements below describe the earlier recovery-only entry point.

The Hero Film is an integration test of the general agent. The latest user instruction replaces the earlier 75-second/eight-shot acceptance target. The current fixture requests three accepted shots and 20 seconds; individual failed shots are omitted and remain available for human review. Final aesthetic approval is still pending.

## A. General architecture

- Contracts: semantic reference roles, exact version/hash bindings, accepted limitations, human-acceptance evidence, recovery plans, candidate acceptance policy and provider conditioning capabilities.
- ReferenceResolver: composes distinct identity, wardrobe/body, environment, prop and style references by role; validates exact pins and human evidence; never silently drops a hard reference to meet a provider limit.
- Human review: `accept_reference_bundle` records “human accepted with known limitations”, retains original negative VLM results, supersedes only explicitly authorized technical reviews, and preserves final human review.
- Provider transport: Kontext advertises `reference_sheet`, not native multi-reference. Every selected image contributes actual pixels to a deterministic, labelled source panel. Source SHA, panel roles and exact input hashes are recorded in provenance and the server echoes the source SHA.
- Workflow: caller-supplied recovery plan, independent per-shot dependencies, frame gate before video generation, targeted repair, finite dispatch/material budgets and configurable early candidate export.
- Quality: role-scoped usability, required observable blocking evidence, separate invalid-critic and material-failure accounting, explicit policy/config revisions and draft quality profile. Major/critical defects remain blocking.
- Application: typed reference-acceptance command and API endpoint for engines configured with a recovery plan. Recovery remains an explicit production-engine capability; the default application factory does not silently opt every project into recovery.

Relevant code: `quality/recovery.py`, `quality/references.py`, `quality/reference_policy.py`, `quality/budget.py`, `media/contracts.py`, `media/reference_conditioning.py`, `providers/flux_kontext.py`, `services/recovery_authorization.py`, `services/reference_recovery.py`, `application/reference_commands.py`.

## B. Integration-test material

`tests/fixtures/hero_recovery.json` contains the film's project, characters, prompts, shot priorities, exact approved reference versions and historical budget amendment. `scripts/run_p5r.py` loads that fixture and verifies preservation of the existing project and audio. Morning Review and acceptance scripts report test evidence. They are not reusable production policy.

The working tree also contains earlier P4/P5 work; Git does not provide a reliable isolated “26 files” baseline. This checkpoint classifies responsibilities rather than attributing all current changes to this recovery turn.

## C. Testcase-specific patches

Removed from production services: character-name voice override, fixed core-shot list, fixed scene/duration acceptance bounds, fixed reference instructions and a character-specific pair-acceptance helper. These are now fixture data or typed policy. Production modules contain no branch on the Hero project ID, character names or approved artifact hashes. Historical `p5r` policy/artifact identifiers remain for budget and provenance compatibility; they do not select a particular movie.

## D. Complementary references

Implemented generically end to end in code: role bindings → resolver → first-frame request → provider source image bytes → provenance. Tests use unrelated entity IDs and inspect the actual HTTP image payload. Human-accepted references are not relabelled as VLM PASS and are not sent through another reference acceptance loop. Downstream frame/video gates judge newly generated outputs.

This is a reference-panel transport over the existing single-source Kontext provider. Its real visual identity retention still requires the integration run; passing payload tests does not prove visual fidelity.

## E. Remaining platform acceptance

Pending live evidence: complementary conditioning in real frames, real frame-gate outcomes, H3 video generation, video critic, targeted repair when triggered, independent continuation after failures, and audio/post candidate export under the revised policy. Native multi-image conditioning is not supported by the installed Kontext service. The typed human-reference command is exposed; a dedicated interactive role-binding editor is not yet implemented.

Validation before resume: targeted resolver, provider payload, review preservation, quality/budget, DAG, runtime, application and audio regression tests. API schema/types are regenerated with the changed contracts. Live outcomes belong in the acceptance report and must not be inferred from unit tests.

Recorded checks: 59 targeted backend tests passed; 50 frontend tests passed; OpenAPI generation and frontend build passed (existing bundle-size warning). Full backend run: 380 passed, four opt-in integrations skipped, two PowerShell installer tests blocked by the child shell's execution policy. Rerunning the ten installer tests with a process-only execution policy passed all ten; no system policy or real SSH credentials were changed. `git diff --check` passed.

Live evidence after resume: the user's exact portrait v3 + wardrobe v5 acceptance is committed as `human_reference_acceptance_01`. Sky environment v3 passed config2 VLM. The first real `frame_SCENE_001-SHOT-01_first@v1` was generated by `flux_kontext`, with four source panels (portrait v3, wardrobe v5, environment v3, prop v1), source sheet SHA `a04add50f1d42f18ed856e6affc4382890bdb309f312e4c46280d388e9ce6e6c` and server-verified source integrity. Inference took 101.03 seconds. This proves real conditioning transport; frame identity usability is still subject to Frame Gate. The first/last pair has been generated and entered that gate.
