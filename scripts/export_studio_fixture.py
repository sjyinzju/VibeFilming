"""Generate a frontend test snapshot from the actual application contract."""
from pathlib import Path
from movie_agent.application.studio import studio_snapshot
from movie_agent.application.creative_inputs import CreateProjectInput
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_api import service_at

root = Path(__file__).resolve().parents[1]
service = service_at(root / 'workspace' / 'p2b-fixture', FakeReasoningProvider())
record = service.create(CreateProjectInput(story_description='An archivist hears tomorrow.').canonical_brief())
path = root / 'web' / 'src' / 'test' / 'snapshot.json'
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(studio_snapshot(service, record.project.project_id).model_dump_json(indent=2), encoding='utf-8')
