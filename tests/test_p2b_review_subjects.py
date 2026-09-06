"""Human review evidence is read-only, versioned, and graph-addressed."""
import asyncio
from datetime import timedelta

import pytest

from movie_agent.application.studio import studio_snapshot
from movie_agent.application.review_subjects import project_review_subject
from movie_agent.domain import Provenance
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_api import service_at
from tests.test_p2a_runtime import brief


@pytest.mark.parametrize('approved', [True, False])
def test_story_subject_history_and_restart(tmp_path, approved):
    async def scenario():
        service = service_at(tmp_path, FakeReasoningProvider())
        pid = service.create(brief()).project.project_id
        service.start(pid)
        await service.tasks[pid]
        snapshot = studio_snapshot(service, pid)
        review = snapshot.reviews[0]
        subject = snapshot.review_subjects[0]
        source = subject.sources[0]
        assert source.source_node_id == 'story_planning'
        assert source.role_result.invocation.role_id == 'story_architect'
        assert source.content.output == snapshot.project.story_bible
        assert source.role_result.output == snapshot.project.story_bible.model_dump(mode='json')
        original = subject.model_dump(mode='json')
        engine = service.engine(pid)
        # A later version of the same identity must never replace historical evidence.
        artifact = source.artifacts[0]
        changed = source.role_result.model_copy(deep=True)
        changed.output['synopsis'] = 'Later revision, not what the user reviewed'
        engine.artifact_store.create_placeholder(artifact.artifact_type, changed.output,
            artifact_id=artifact.artifact_id, provenance=Provenance(
                parameters={'role_result': changed.model_dump(mode='json')}))
        engine.human_gates.resolve(review.review_id, approved=approved, notes='A real decision')
        engine._save_checkpoint(engine.current_project, engine.current_production)
        assert studio_snapshot(service, pid).review_subjects[0].model_dump(mode='json') == original
        restored = service_at(tmp_path, FakeReasoningProvider())
        restored_snapshot = studio_snapshot(restored, pid)
        assert restored_snapshot.reviews[0].status == ('approved' if approved else 'rejected')
        assert restored_snapshot.review_subjects[0].model_dump(mode='json') == original
        # Equal timestamps must not override authoritative journal ordering.
        later = artifact.model_copy(deep=True)
        later.version = 2
        later.created_at = review.requested_at
        later.provenance.parameters['role_result'] = changed.model_dump(mode='json')
        tied = project_review_subject(review, snapshot.graph, [artifact, later], events=engine.event_bus.events())
        assert tied.sources[0].role_result.output == source.role_result.output

        # Opaque IDs and reordered nodes still resolve through edges + invocation relation.
        graph = snapshot.graph.model_copy(deep=True)
        renamed = {'story_planning': 'opaque-source-92', review.node_id: 'opaque-gate-17'}
        for node in graph.nodes:
            node.node_id = renamed.get(node.node_id, node.node_id)
        for edge in graph.edges:
            edge.source_node_id = renamed.get(edge.source_node_id, edge.source_node_id)
            edge.target_node_id = renamed.get(edge.target_node_id, edge.target_node_id)
        graph.nodes.reverse()
        renamed_review = review.model_copy(update={'node_id': 'opaque-gate-17'})
        artifacts = [artifact.model_copy(deep=True)]
        artifacts[0].provenance.parameters['role_result']['invocation']['node_id'] = 'opaque-source-92'
        assert project_review_subject(renamed_review, graph, artifacts).sources[0].source_node_id == 'opaque-source-92'
        assert project_review_subject(review, snapshot.graph, []).unavailable_reason
        future = artifact.model_copy(update={'created_at': review.requested_at + timedelta(seconds=1)})
        assert not project_review_subject(review, snapshot.graph, [future]).sources
    asyncio.run(scenario())


def test_all_current_gates_have_subjects(tmp_path):
    async def scenario():
        service = service_at(tmp_path, FakeReasoningProvider(), auto_approve=True)
        pid = service.create(brief()).project.project_id
        service.start(pid)
        await service.tasks[pid]
        snapshot = studio_snapshot(service, pid)
        assert snapshot.status == 'completed'
        assert [s.subject_type for s in snapshot.review_subjects] == ['StoryBible', 'ShotPlan', 'FinalCut']
        assert all(s.sources and not s.unavailable_reason for s in snapshot.review_subjects)
        shot = snapshot.review_subjects[1].sources[0]
        assert shot.content.output.model_dump(mode='json') == shot.role_result.output
        assert [s.shot_id for s in shot.content.output.shots] == [s.shot_id for s in snapshot.project.shots]
        final = snapshot.review_subjects[2].sources[0]
        assert final.artifacts[0].artifact_type == 'timeline'
        assert final.artifacts[0].provenance.tool == 'mock_timeline_assembly'
        assert final.source_node_id == 'full_film_review'
        assert final.evaluations and final.evaluations[0].passed
    asyncio.run(scenario())
