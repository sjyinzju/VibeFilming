"""Choose persisted engine capability, never a project/character identifier."""
from pathlib import Path
from movie_agent.artifacts import LocalArtifactStore
from movie_agent.services.film_production import FilmProduction
from movie_agent.services.reference_recovery import ReferenceRecoveryProduction
from movie_agent.services.reasoning_production import ReasoningMovieProduction


def production_engine(workspace,provider,**kwargs):
    workspace=Path(workspace)
    store=LocalArtifactStore(workspace/'artifacts')
    if store.get('production_policy'):
        engine_type=FilmProduction
    elif store.get('recovery_plan'):
        engine_type=ReferenceRecoveryProduction
    elif any((workspace/'checkpoints').glob('*/LATEST')):
        engine_type=ReasoningMovieProduction  # Existing projects retain their persisted workflow.
    else:
        engine_type=FilmProduction
    return engine_type(workspace,provider,**kwargs)
