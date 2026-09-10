"""Explicit user acceptance of exact complementary references through the application boundary."""
from .repository import ProductionStatus
from .service import CommandConflict
from .views import CommandAccepted
from movie_agent.services.recovery_authorization import accept_reference_bundle


def accept_references(service,project_id,command):
    record=service.repository.get(project_id)
    if record.status in {ProductionStatus.RUNNING,ProductionStatus.PAUSING,ProductionStatus.CANCELLED}:
        raise CommandConflict('Reference acceptance requires an idle project')
    engine=service.engine(project_id)
    if not hasattr(engine,'recovery_plan') or engine.artifact_store.get('recovery_plan') is None:
        raise CommandConflict('Configure a recovery plan before accepting a reference bundle')
    before=engine.pack().model_dump(mode='json')
    accept_reference_bundle(engine,command)
    engine.install_recovery()
    if engine.pack().model_dump(mode='json')!=before:
        from .reference_invalidation import invalidate_reference_dependents
        invalidate_reference_dependents(engine,{b.subject_id for b in command.bindings})
    record.status=ProductionStatus.PAUSED;record.failure_code=None
    service.repository.save(record)
    return CommandAccepted(project_id=project_id,status=record.status)
