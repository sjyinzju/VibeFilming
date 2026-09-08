"""Typed human media commands over the existing Human Gate and checkpoint."""

from hashlib import sha256
from movie_agent.domain import EventType, HumanGateType, Provenance, ReviewStatus, WorkflowNodeStatus, utc_now
from movie_agent.media import HumanRepairDirective, HumanRepairInput
from movie_agent.media.inspection import media_hash
from .repository import ProductionStatus as Status
from .service import CommandConflict


def _existing(engine, command):
    previous = next((d for d in engine.human_media_directives if d.command_id == command.command_id), None)
    if previous:
        original = HumanRepairInput.model_validate({k: getattr(previous, k) for k in HumanRepairInput.model_fields})
        if original != command:
            raise CommandConflict("Command ID was already used with different feedback")
    return previous


def _inspection(engine, command):
    inspection = next((i for i in engine.media_runtime.inspections if i.result_id == command.inspection_result_id), None)
    if inspection is None or (inspection.target_artifact_id, inspection.target_artifact_version, inspection.target_sha256) != (
        command.target_artifact_id, command.target_artifact_version, command.target_sha256
    ):
        raise CommandConflict("Feedback must address the exact inspected artifact version and SHA")
    versions = engine.artifact_store.list_versions(command.target_artifact_id)
    if not versions or versions[-1].version != command.target_artifact_version:
        raise CommandConflict("A newer video version exists; review that version before submitting feedback")
    if media_hash(engine.binary_store, versions[-1]) != command.target_sha256:
        raise CommandConflict("Inspected media content no longer matches its SHA")
    issue_ids = {i.issue_id for i in inspection.issues}
    if not set(command.accepted_issue_ids + command.dismissed_issue_ids) <= issue_ids:
        raise CommandConflict("Feedback contains issue IDs from another inspection")
    return inspection


def resolve_media_directive(service, engine, review, command):
    previous = _existing(engine, command)
    if previous:
        if previous.review_id != review.review_id:
            raise CommandConflict("Command belongs to a different review")
        return engine.human_gates.get(review.review_id)
    if review.status != ReviewStatus.PENDING or review.superseded_at is not None:
        raise CommandConflict("Review is already resolved or superseded")
    if review.gate_type != HumanGateType.AGENT_ESCALATION or review.inspection_result_id != command.inspection_result_id:
        raise CommandConflict("This review does not accept a media repair directive")
    inspection = _inspection(engine, command)
    directive = HumanRepairDirective(**command.model_dump(exclude={"schema_version"}),
        directive_id="directive_" + sha256((review.project_id + ':' + command.command_id).encode()).hexdigest()[:32],
        project_id=review.project_id, review_id=review.review_id,
        shot_id=inspection.shot_id, scene_id=inspection.scene_id)
    engine.human_media_directives.append(directive)
    engine.human_gates._reviews[review.review_id] = review.model_copy(update={"directive_id": directive.directive_id})
    result = engine.human_gates.resolve(review.review_id, True, command.feedback)
    # The checkpoint atomically records review resolution + directive before events
    # invite workflow resume. Artifact evidence is recoverable from that checkpoint.
    engine._durable_media_checkpoint()
    persist_directive(engine, directive)
    engine._emit(EventType.HUMAN_MEDIA_DIRECTIVE_CREATED, review.project_id,
        {"directive_id": directive.directive_id, "review_id": review.review_id,
         "inspection_result_id": inspection.result_id, "disposition": directive.disposition.value,
         "target_artifact_id": directive.target_artifact_id, "target_artifact_version": directive.target_artifact_version},
        node_id="repair_accept")
    return result


def persist_directive(engine, directive):
    if engine.artifact_store.get(directive.directive_id) is None:
        engine.artifact_store.create_structured(directive.directive_id, directive.model_dump(mode="json"),
            metadata={"purpose": "human_media_directive"}, parent_artifact_ids=[directive.target_artifact_id],
            provenance=Provenance(role="Human", tool="media_review", project_id=directive.project_id,
                shot_id=directive.shot_id, parameters={"target_artifact_version": directive.target_artifact_version,
                                                       "target_sha256": directive.target_sha256}))


def recover_directives(engine):
    for directive in engine.human_media_directives:
        persist_directive(engine, directive)


def submit_media_feedback(service, project_id, command):
    engine = service.engine(project_id)
    previous = _existing(engine, command)
    if previous:
        return engine.human_gates.get(previous.review_id)
    record = service.repository.get(project_id)
    if record.status in {Status.COMPLETED, Status.CANCELLED, Status.PAUSING}:
        raise CommandConflict("Shot feedback is available before final acceptance; completed project reopening is not supported")
    inspection = _inspection(engine, command)
    if any(j.shot_id == inspection.shot_id and j.status.value in {
        "running", "evaluating", "preparing_model", "preparing", "uploading", "repairing", "waiting_resource"
    } for j in engine._all_jobs()):
        raise CommandConflict("Wait for the current shot operation to finish before submitting feedback")
    if any(r.status == ReviewStatus.PENDING and r.superseded_at is None and r.node_id != "final_gate"
           for r in engine.human_gates.all()):
        raise CommandConflict("Resolve the pending review using its media action controls")
    graph = engine.current_production
    if graph.node("visual_semantic_critic").status == WorkflowNodeStatus.PENDING:
        raise CommandConflict("Shot feedback requires an existing quality review")
    needs_revision = graph.node("repair_accept").status == WorkflowNodeStatus.SUCCEEDED
    if needs_revision and record.status in {Status.RUNNING, Status.PAUSING}:
        raise CommandConflict("Pause production before revising a shot used by downstream work")
    if needs_revision:
        # Scoped media revision: invalidate only repair_accept and its descendants.
        # Canonical planning, frame production, successful video jobs and all history
        # remain intact. The newly typed directive chooses exactly one shot to repair.
        affected = {"repair_accept"}
        for node in sorted(graph.graph.nodes, key=lambda n: n.display_order or 0):
            if affected.intersection(node.dependencies):
                affected.add(node.node_id)
        for node in graph.graph.nodes:
            if node.node_id in affected:
                node.status, node.progress, node.completed_at = WorkflowNodeStatus.PENDING, 0, None
        for review in engine.human_gates.all():
            if review.node_id in affected:
                engine.human_gates._reviews[review.review_id] = review.model_copy(update={"superseded_at": utc_now()})
        engine.current_project.canonical_state.completed_node_ids = [
            n.node_id for n in graph.graph.nodes if n.status == WorkflowNodeStatus.SUCCEEDED]
        record.status = Status.PAUSED
        service.repository.save(record)
    review = engine.human_gates.request(project_id, "repair_accept", HumanGateType.AGENT_ESCALATION,
        "User requested shot-level media feedback", [inspection.target_artifact_id], inspection=inspection)
    return resolve_media_directive(service, engine, review, command)
