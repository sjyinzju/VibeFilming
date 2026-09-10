from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from movie_agent.artifacts.store import LocalArtifactStore
from movie_agent.domain import (Shot,ShotNarrative,CameraSpec,ShotSize,LightingSpec,Scene,VisualBible,
    CharacterState,ContinuityState,Evaluation,EvaluationLayer,Artifact,ArtifactType,QualityProfile)
from movie_agent.media import (VisionInspectionResult,VisionScore,VisionInspectionProfile as VP,VisionDecision,
    ReferenceType,MediaIssue,MediaIssueType)
from movie_agent.quality.references import IdentityReference,ReferenceIdentitySet,ReferenceRole,ReferenceResolver
from movie_agent.quality.budget import QualityBudgetLedger,QualityBudget,BudgetExhausted,RepairCostPolicy,WorkKind
from movie_agent.quality.reports import aggregate_shot,aggregate_film,ShotQualityStatus,EvidenceStatus


def shot():
    return Shot(shot_id='shot1',scene_id='s',narrative=ShotNarrative(purpose='reveal',beat='choice',dialogue=['hello']),
        duration_seconds=10,camera=CameraSpec(shot_size=ShotSize.MEDIUM),lighting=LightingSpec(setup='soft'),
        state_before=ContinuityState(character_states={'c':CharacterState(character_id='c',wardrobe='grey')}))


def anchor(kind,subject,role):
    return IdentityReference(artifact_id='ref_'+subject,version=1,sha256='a'*64,reference_type=kind,
        reference_role=role,subject_id=subject,selected=True,quality_status=VisionDecision.PASS,inspection_result_id='baseline')


def test_reference_selection_is_scoped_and_pinned():
    pack=ReferenceIdentitySet(references=[anchor(ReferenceType.STYLE,'v',ReferenceRole.STYLE),
        anchor(ReferenceType.CHARACTER,'unrelated',ReferenceRole.PORTRAIT),
        anchor(ReferenceType.LOCATION,'loc',ReferenceRole.ENVIRONMENT),anchor(ReferenceType.CHARACTER,'c',ReferenceRole.PORTRAIT)])
    result=ReferenceResolver().resolve(shot(),Scene(scene_id='s',title='S',purpose='p',location_id='loc',time_description='night'),
        VisualBible(visual_bible_id='v',style_statement='natural'),pack)
    assert [r.entity_id for r in result.references]==['c','loc','v']
    assert all(r.version==1 and r.sha256=='a'*64 for r in result.references)
    assert len(result.reasons)==3
    bounded=ReferenceResolver(max_references=2).resolve(shot(),SimpleNamespace(location_id='loc'),SimpleNamespace(visual_bible_id='v'),pack)
    assert len(bounded.hard_required_refs)==2 and bounded.missing_soft_refs==['style:v:reference_bound']
    with pytest.raises(ValueError,match='exceed'):
        ReferenceResolver(max_references=1).resolve(shot(),SimpleNamespace(location_id='loc'),SimpleNamespace(visual_bible_id='v'),pack)


def test_reference_unreviewed_cannot_be_selected():
    with pytest.raises(ValidationError):
        IdentityReference(artifact_id='x',version=1,sha256='a'*64,reference_type='character',
                          reference_role='portrait',subject_id='c',selected=True)


def test_budget_durable_resume_and_global_limit(tmp_path):
    store=LocalArtifactStore(tmp_path)
    ledger=QualityBudgetLedger(store)
    for i in range(18): assert ledger.reserve('video_regenerate','s'+str(i),inputs={'v':1},config={})[1]
    resumed=QualityBudgetLedger(LocalArtifactStore(tmp_path))
    assert resumed.reserve('video_regenerate','s0',inputs={'v':1},config={})[1] is False
    with pytest.raises(BudgetExhausted): resumed.reserve('video_regenerate','new',inputs={},config={})
    assert len(resumed.records())==18


@pytest.mark.parametrize('kind,limit',[('video_regenerate',2),('frame_edit',2),('tts',2),('music',2),('vlm_inspection',3)])
def test_per_subject_limits(tmp_path,kind,limit):
    ledger=QualityBudgetLedger(LocalArtifactStore(tmp_path))
    for i in range(limit): ledger.reserve(kind,'same',inputs={'revision':i},config={})
    with pytest.raises(BudgetExhausted): ledger.reserve(kind,'same',inputs={'revision':limit},config={})


def test_cost_routes_avoid_unrelated_video():
    policy=RepairCostPolicy()
    for issue,kind in [('identity_drift',WorkKind.FRAME_EDIT),('dialogue_voice',WorkKind.TTS),
                       ('pacing',WorkKind.POST),('action_incomplete',WorkKind.VIDEO),('music_masking',WorkKind.POST)]:
        assert policy.route(issue,kontext_available=True).kind==kind
    assert policy.route('identity_drift',kontext_available=False).kind==WorkKind.FRAME


def quality_inputs():
    artifact=Artifact(artifact_id='video',version=2,artifact_type=ArtifactType.VIDEO,uri='artifact://video/v2',metadata={'sha256':'b'*64})
    inspections=[VisionInspectionResult(request_id='r',target_artifact_id='video',target_artifact_version=2,target_sha256='b'*64,
        provider_id='qwen3_vl',scores=[VisionScore(profile=VP.CHARACTER_IDENTITY,score=.92)],
        evidence=['same face'],decision=VisionDecision.PASS,summary='matched')]
    evaluations=[Evaluation(layer=layer,target_artifact_id='video',target_artifact_version=2,score=.9,passed=True)
                 for layer in (EvaluationLayer.CINEMATIC,EvaluationLayer.TECHNICAL_QC)]
    return artifact,inspections,evaluations


def test_quality_uses_exact_versions_and_keeps_unknown_audio():
    artifact,inspections,evaluations=quality_inputs()
    inspections.append(inspections[0].model_copy(update={'target_artifact_version':1,'decision':VisionDecision.HUMAN_REVIEW}))
    report=aggregate_shot(shot(),artifact,evaluations,inspections)
    assert report.status==ShotQualityStatus.ACCEPTED
    assert len(report.inspection_ids)==1
    assert report.dimensions['dialogue_intelligibility_status'].status==EvidenceStatus.HUMAN_REQUIRED
    assert report.dimensions['wardrobe_consistency'].status==EvidenceStatus.UNKNOWN
    film=aggregate_film('p',[report])
    assert film.accepted_seconds==10 and not film.minimum_runtime_met and not film.human_aesthetically_approved


def test_missing_or_blocking_evidence_never_passes():
    artifact,inspections,evaluations=quality_inputs()
    assert aggregate_shot(shot(),artifact,[],inspections).status!=ShotQualityStatus.ACCEPTED
    inspections[0].decision=VisionDecision.HUMAN_REVIEW
    inspections[0].issues=[MediaIssue(issue_type=MediaIssueType.IDENTITY_DRIFT,severity='major',message='different face',evidence=['face'])]
    assert aggregate_shot(shot(),artifact,evaluations,inspections).status!=ShotQualityStatus.ACCEPTED


def test_hero_import_and_showcase_profile():
    from movie_agent.services.hero_production import HeroMovieProduction
    assert QualityProfile.SHOWCASE.value=='showcase'


def test_reinspection_supersedes_current_verdict_without_losing_source_history():
    artifact,inspections,evaluations=quality_inputs()
    old=inspections[0].model_copy(update={'result_id':'old','decision':VisionDecision.HUMAN_REVIEW,
        'scores':[VisionScore(profile=VP.CHARACTER_IDENTITY,score=.4)]})
    sources=[old,*inspections]
    report=aggregate_shot(shot(),artifact,evaluations,sources)
    assert report.status==ShotQualityStatus.ACCEPTED
    assert report.inspection_ids==[inspections[0].result_id]
    assert len(sources)==2 and sources[0].decision==VisionDecision.HUMAN_REVIEW
