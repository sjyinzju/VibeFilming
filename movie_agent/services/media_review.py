"""Bounded media state machine inside the existing repair_accept DAG node."""

from movie_agent.domain import (ArtifactType, EventType, GenerationJob, HumanGateType,
    JobStatus, Provenance, ResourceClass, ReviewStatus, WorkflowNodeStatus)
from movie_agent.media import (MediaRepairActionType as A, VisionDecision as D,
    HumanRepairDisposition as H, ReferenceType, MediaReference, ImageGenerationRequest,
    ImageGenerationMode, ImagePurpose, MediaCapabilityRequirement)
from movie_agent.media.compilers import GenericImagePromptCompiler, repair_prompt_sections


def inspection_references(engine, project, shot, video):
    refs = engine._media_references(project, shot)
    bindings = (video.provenance.parameters.get("input_artifact_versions") or
                video.provenance.parameters.get("video_preflight_input_artifacts") or [])
    frozen = {b["artifact_id"]: b for b in bindings if isinstance(b, dict) and "artifact_id" in b}
    anchors, relevant = [], []
    for ref in refs:
        bound = frozen.get(ref.artifact_id)
        if bound:
            ref = ref.model_copy(update={"version": bound["version"], "sha256": bound.get("sha256")})
        if ref.reference_type in {ReferenceType.FIRST_FRAME, ReferenceType.LAST_FRAME}:
            anchors.append(ref)
        elif ref.selected and ref.reference_type in {ReferenceType.CHARACTER, ReferenceType.LOCATION,
                                                     ReferenceType.PROP, ReferenceType.STYLE}:
            relevant.append(ref)
    # Stable bounded selection from the already shot-scoped reference resolver.
    seen = set()
    return [r for r in [*anchors, *sorted(relevant, key=lambda r: (r.reference_type.value, r.artifact_id))[:6]]
            if not ((r.artifact_id, r.version) in seen or seen.add((r.artifact_id, r.version)))]


async def supported_repairs(engine):
    from movie_agent.quality.vision import supported_repair_actions
    return supported_repair_actions(await engine.media_runtime.capabilities())


def escalate(engine, project, inspection, reason):
    pending = engine.human_gates.pending_for_node("repair_accept")
    if pending is None:
        engine.human_gates.request(project.project_id, "repair_accept", HumanGateType.AGENT_ESCALATION,
            reason, [inspection.target_artifact_id], inspection=inspection)
    engine.current_production.set_status("repair_accept", WorkflowNodeStatus.WAITING_HUMAN)
    engine._durable_media_checkpoint()
    return False


async def repair_frames(engine, project, shot, plan, state):
    image_caps = next((c.image for c in await engine.media_runtime.capabilities() if c.image), None)
    for action, position, purpose in [(A.REGENERATE_FIRST_FRAME, "first", ImagePurpose.FIRST_FRAME),
                                      (A.REGENERATE_LAST_FRAME, "last", ImagePurpose.LAST_FRAME)]:
        if action not in {a.action_type for a in plan.actions}:
            continue
        identity = f"frame_{shot.shot_id}_{position}"
        versions = engine.artifact_store.list_versions(identity)
        state.setdefault("frame_versions", {})
        if identity not in state["frame_versions"]:
            state["frame_versions"][identity] = len(versions) + 1
            engine._durable_media_checkpoint()
        version = state["frame_versions"][identity]
        existing = engine.artifact_store.get(identity, version)
        if existing:
            if plan.repair_plan_id not in existing.provenance.repair_plan_ids:
                raise ValueError("frame repair version belongs to another plan")
            continue
        previous = engine.artifact_store.get(identity, version - 1)
        if previous is None:
            raise ValueError("frame repair requires an existing immutable anchor")
        source = MediaReference(reference_type=ReferenceType.SOURCE_IMAGE, artifact_id=identity, version=previous.version)
        prompt = GenericImagePromptCompiler().compile(shot, purpose, [])
        sections = [*prompt.sections, *repair_prompt_sections(plan.repair_context)]
        prompt = prompt.model_copy(update={"sections": sections,
            "positive_prompt": "\n".join(f"[{s.name}] {s.content}" for s in sections)})
        job_id = f"repair-frame:{shot.shot_id}:{position}:v{version}"
        mode = ImageGenerationMode.IMAGE_TO_IMAGE if image_caps.image_to_image else ImageGenerationMode.TEXT_TO_IMAGE
        request = ImageGenerationRequest(job_id=job_id, project_id=project.project_id, scene_id=shot.scene_id,
            shot_id=shot.shot_id, prompt_package=prompt, source_image=source if image_caps.image_to_image else None,
            mode=mode, purpose=purpose, output_artifact_id=identity,
            width=previous.metadata["width"], height=previous.metadata["height"], aspect_ratio=project.brief.aspect_ratio,
            required_capabilities=[MediaCapabilityRequirement(capability=mode.value)])
        job = GenerationJob(job_id=job_id, idempotency_key=job_id, project_id=project.project_id,
            node_id="repair_accept", scene_id=shot.scene_id, shot_id=shot.shot_id,
            task="frame", resource_class=ResourceClass.MEDIUM, input_artifact_ids=[identity],
            provenance=Provenance(project_id=project.project_id, shot_id=shot.shot_id,
                repair_plan_ids=[plan.repair_plan_id], evaluation_ids=[plan.inspection_result_id]))
        await engine.media_runtime.generate_image(job, request, artifact_type=ArtifactType.FRAME)
        engine._durable_media_checkpoint()


async def run_repair_loop(engine, project):
    supported = await supported_repairs(engine)
    for shot in project.shots:
        while True:
            # Resume the exact pending attempt before allocating any new version.
            run = next((s for s in engine._repair_runs.values() if s["shot_id"] == shot.shot_id and s["status"] != "completed"), None)
            if run:
                plan = next(p for p in engine.media_repair_plans if p.repair_plan_id == run["plan_id"])
                await repair_frames(engine, project, shot, plan, run)
                video = (await engine._generate_versions(project, [shot],
                    repair_plan_ids={shot.shot_id: plan.repair_plan_id},
                    repair_contexts={shot.shot_id: plan.repair_context},
                    target_versions={shot.shot_id: run["output_version"]}))[0]
                run["status"] = "reinspecting"
                engine._durable_media_checkpoint()
                for evaluation in [engine.technical_qc.evaluate(shot, video),
                                   await engine._vision_evaluation(project, shot, video),
                                   engine.cinematic_critic.evaluate(shot, video)]:
                    engine._record_evaluation(project, evaluation)
                run["status"] = "completed"
                if plan.directive_id:
                    engine._applied_directives.add(plan.directive_id)
                engine._emit(EventType.MEDIA_REPAIR_COMPLETED, project.project_id,
                    {"repair_plan_id": plan.repair_plan_id, "shot_id": shot.shot_id,
                     "target_artifact_version": video.version}, node_id="repair_accept")
                engine._emit(EventType.REPAIR_COMPLETED, project.project_id,
                             {"repair_plan_id": plan.repair_plan_id, "shot_id": shot.shot_id})
                engine._durable_media_checkpoint()
                continue

            video = engine._latest_video(shot.shot_id)
            evaluation = await engine._vision_evaluation(project, shot, video)
            engine._record_evaluation(project, evaluation)
            inspection = next(i for i in engine.media_runtime.inspections if i.result_id == evaluation.inspection_result_id)
            directive = next((d for d in reversed(engine.human_media_directives)
                if d.shot_id == shot.shot_id and d.target_artifact_version == video.version
                and d.inspection_result_id == inspection.result_id and d.directive_id not in engine._applied_directives), None)
            if directive and directive.disposition == H.KEEP_CURRENT:
                engine._select(project, video.artifact_id, video.version)
                engine.human_overrides.append({"directive_id": directive.directive_id, "inspection_result_id": inspection.result_id,
                    "target_artifact_id": video.artifact_id, "target_artifact_version": video.version,
                    "target_sha256": inspection.target_sha256, "ai_decision": inspection.decision.value,
                    "feedback": directive.feedback})
                engine._applied_directives.add(directive.directive_id)
                engine._durable_media_checkpoint()
                break
            if any(o["inspection_result_id"] == inspection.result_id for o in engine.human_overrides):
                engine._select(project, video.artifact_id, video.version)
                break
            qc = engine.technical_qc.evaluate(shot, video)
            cinematic = engine.cinematic_critic.evaluate(shot, video)
            for e in [qc, cinematic]:
                engine._record_evaluation(project, e)
            if inspection.decision == D.PASS and qc.passed and cinematic.passed and directive is None:
                engine._select(project, video.artifact_id, video.version)
                break
            retry_count = video.version - 1
            plan = next((p for p in reversed(engine.media_repair_plans)
                if p.inspection_result_id == inspection.result_id and p.directive_id == (directive.directive_id if directive else None)), None)
            if plan is None:
                plan = engine.repair_planner.plan_media(inspection, retry_budget=shot.retry_budget,
                    retry_count=retry_count, supported_actions=supported, directive=directive)
                engine.media_repair_plans.append(plan)
                # Preserve the established Evaluation/RepairPlan read model.
                legacy = engine.repair_planner.plan(evaluation, retry_budget=max(shot.retry_budget, retry_count), retry_count=retry_count)
                legacy = legacy.model_copy(update={"repair_plan_id": plan.repair_plan_id})
                engine.repair_plans.append(legacy)
                engine.artifact_store.create_structured(plan.repair_plan_id, plan.model_dump(mode="json"),
                    metadata={"purpose": "media_repair_plan"}, provenance=inspection.provenance,
                    parent_artifact_ids=[video.artifact_id])
                engine._durable_media_checkpoint()
            if (plan.requires_human or (inspection.decision == D.HUMAN_REVIEW and directive is None)
                or not qc.passed or not cinematic.passed):
                if directive:
                    engine._applied_directives.add(directive.directive_id)
                return escalate(engine, project, inspection, "Review this video version: evidence, repair capability or automatic budget requires your decision.")
            engine._repair_runs[plan.repair_plan_id] = {"plan_id": plan.repair_plan_id, "shot_id": shot.shot_id,
                "source_version": video.version, "output_version": video.version + 1, "status": "repairing"}
            engine._durable_media_checkpoint()  # allocation is durable before dispatch
            engine._emit(EventType.MEDIA_REPAIR_STARTED, project.project_id,
                {"repair_plan_id": plan.repair_plan_id, "shot_id": shot.shot_id}, node_id="repair_accept")
            engine._emit(EventType.REPAIR_STARTED, project.project_id,
                         {"repair_plan_id": plan.repair_plan_id, "shot_id": shot.shot_id})
    return True
