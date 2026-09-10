"""Opt-in real or offline fixture post E2E server; never production wiring."""
import os
from pathlib import Path
from movie_agent.api.app import create_app
from movie_agent.application.post_commands import reexport, PostExportCommand
from scripts.accept_post import prepare,service_at,ROOT

real=os.environ.get('MOVIE_AGENT_RUN_POST_INTEGRATION')=='1'
root=ROOT if real else Path(os.environ.get('MOVIE_AGENT_POST_TEST_ROOT','workspace/p4c-post-e2e'))
pid=prepare(root,real)
service=service_at(root)
app=create_app(service)


@app.post('/p4c/start')
async def begin():
    engine=service.engine(pid)
    if any(a.metadata.get('render_plan',{}).get('policy_version')=='p4c.2' for a in engine.artifact_store.list_versions('rough_cut')):
        return {'project_id':pid,'status':service.repository.get(pid).status}
    return reexport(service,pid,PostExportCommand())


@app.get('/p4c/project')
async def project():
    return {'project_id':pid,'real_acceptance':real}
