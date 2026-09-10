"""Project-independent production policy and canonical reference planning."""
from pydantic import Field
from math import ceil
from movie_agent.quality.budget import QualityBudget
from movie_agent.domain import ContractModel
from movie_agent.media import ReferenceType as R
from movie_agent.quality.recovery import RecoveryPlan, ReferenceRecoveryAction, CandidateAcceptancePolicy
from movie_agent.quality.references import ReferenceRole, hard_reference_subjects


class FilmProductionPolicy(ContractModel):
    revision: str = 'film-production-1'
    review_planning: bool = False
    require_real_providers: bool = True
    candidate: CandidateAcceptancePolicy | None = None
    minimum_candidate_coverage: float = Field(default=.8, gt=0, le=1)
    quality_budget: QualityBudget | None = None


def plan_references(project,policy):
    if not project.shots or not project.scenes or not project.visual_bible:
        raise ValueError('Reference planning requires canonical scenes, shots and visual bible')
    descriptors=[]
    for character in project.characters:
        wardrobe=list(dict.fromkeys(state.wardrobe for shot in project.shots
            for state in [shot.state_before.character_states.get(character.character_id)] if state and state.wardrobe))
        descriptors.append((character.character_id,R.CHARACTER,'character_plate',
            [ReferenceRole.PORTRAIT,ReferenceRole.WARDROBE,ReferenceRole.SILHOUETTE],
            'One full-body canonical character, face clearly readable: '+character.name+'. '+character.identity_description+
            '. '+ '; '.join([*character.appearance_constraints,*wardrobe])))
    for location in project.locations:
        descriptors.append((location.location_id,R.LOCATION,'location_plate',[ReferenceRole.ENVIRONMENT],
            'Unoccupied canonical environment: '+location.name+'. '+location.description+'. '+'; '.join(location.immutable_features)))
    required={subject for shot in project.shots for kind,subject in hard_reference_subjects(shot,
        next(scene for scene in project.scenes if scene.scene_id==shot.scene_id)) if kind==R.PROP}
    for prop in project.props:
        if prop.continuity_critical or prop.prop_id in required:
            descriptors.append((prop.prop_id,R.PROP,'prop_plate',[ReferenceRole.PROP],
                'Isolated canonical prop, no people or scenery: '+prop.name+'. '+prop.description))
    bible=project.visual_bible
    descriptors.append((bible.visual_bible_id,R.STYLE,'style_frame',[ReferenceRole.STYLE],
        'Abstract cinematic palette and texture reference, no characters or text: '+bible.style_statement+'. '+'; '.join(bible.palette)))
    actions=[ReferenceRecoveryAction(node_id='production_reference:'+kind.value+':'+subject,subject_id=subject,
        reference_type=kind,artifact_id='identity_'+subject,purpose=purpose,strategy='generate',
        instruction=description,expected_description=description,roles=roles,priority=20+index)
        for index,(subject,kind,purpose,roles,description) in enumerate(descriptors)]
    gates={action.subject_id:action.node_id for action in actions}
    missing={subject for shot in project.shots for _,subject in hard_reference_subjects(shot,
        next(scene for scene in project.scenes if scene.scene_id==shot.scene_id)) if subject not in gates}
    if missing:raise ValueError('Canonical reference subjects missing from planning: '+', '.join(sorted(missing)))
    candidate=policy.candidate or CandidateAcceptancePolicy(
        minimum_seconds=project.brief.target_duration*policy.minimum_candidate_coverage,
        minimum_shots=ceil(len(project.shots)*policy.minimum_candidate_coverage),export_before_optional_work=False)
    return RecoveryPlan(plan_revision=policy.revision,namespace='production',entry_node_id='asset_planning',
        reference_actions=actions,reference_gate_nodes=gates,candidate=candidate,quality_profile=project.brief.quality_level)
