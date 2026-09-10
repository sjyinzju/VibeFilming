"""One existing explicit revision for the diagnosed scene-2 ambient-prop conflict."""
from pathlib import Path
from movie_agent.services.hero_production import HeroMovieProduction
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.domain import Provenance

root=Path('workspace/p5-hero-film');pid=(root/'project-id.txt').read_text().strip()
# No model call or resource ownership during this local checkpoint command.
engine=HeroMovieProduction(root/pid,None,media_settings=MediaProviderSettings())
project,graph=engine._restore();engine.current_project,engine.current_production=project,graph
engine.artifact_store.create_structured('p5_scene2_revision_authorization',{
    'authorization':'P5 overnight autonomous planning and bounded quality repair request',
    'diagnosis':'Local deltas remove canonical ambient held-prop relationships; preserve those relationships.',
    'scope':'one existing revise_failed_role allowance; keep all failed attempts; no human aesthetic approval'},
    provenance=Provenance(project_id=pid,tool='p5_quality_core'))
engine.revise_failed_role('cinematographer')
print('Scene-2 planning revision recorded; all earlier evidence preserved.')
