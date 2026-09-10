"""Aggregated views of existing Evaluation and VisionInspectionResult evidence."""
from enum import StrEnum
from pydantic import Field

from movie_agent.domain import ContractModel, QualityProfile, IssueSeverity, EvaluationLayer
from movie_agent.media import VisionDecision


class EvidenceStatus(StrEnum):
    PASS = 'pass'
    FAIL = 'fail'
    UNKNOWN = 'unknown'
    HUMAN_REQUIRED = 'human_required'


class ShotQualityStatus(StrEnum):
    ACCEPTED = 'accepted'
    REPAIRING = 'repairing'
    HUMAN_REVIEW = 'human_review'
    REJECTED = 'rejected'


class QualityThresholds(ContractModel):
    profile: QualityProfile = QualityProfile.SHOWCASE
    visual_minimum: float = Field(default=.80, ge=0, le=1)
    cinematic_minimum: float = Field(default=.75, ge=0, le=1)
    minimum_accepted_seconds: float = Field(default=75, gt=0)
    minimum_accepted_shots: int = Field(default=8, ge=1)
    target_seconds: float = Field(default=105, gt=0)

    @classmethod
    def for_profile(cls, profile):
        return cls(profile=profile, visual_minimum=.65, cinematic_minimum=.60) if profile == QualityProfile.DRAFT else cls(profile=profile)


VISUAL = ('character_identity', 'wardrobe_consistency', 'location_consistency', 'prop_consistency',
          'style_consistency', 'prompt_alignment', 'action_completion', 'camera_motion',
          'temporal_stability', 'artifact_detection')
AUDIO = ('dialogue_present', 'dialogue_intelligibility_status', 'speaker_identity_status',
         'native_dialogue_overlap', 'music_masking', 'technical_audio_qc')
CINEMATIC = ('composition', 'visual_hierarchy', 'emotional_clarity', 'performance_readability',
             'shot_motivation', 'continuity', 'editing_usefulness')
PROFILE_MAP = {'scene_consistency':'location_consistency', 'character_identity':'character_identity',
               'prompt_alignment':'prompt_alignment', 'action_completion':'action_completion',
               'camera_motion':'camera_motion', 'continuity':'continuity', 'artifact_detection':'artifact_detection'}
ISSUE_MAP = {'identity_drift':'character_identity', 'wardrobe_drift':'wardrobe_consistency',
             'scene_drift':'location_consistency', 'prop_drift':'prop_consistency',
             'style_mismatch':'style_consistency', 'temporal_flicker':'temporal_stability',
             'action_incomplete':'action_completion', 'motion_failure':'action_completion',
             'continuity_error':'continuity', 'speech_error':'native_dialogue_overlap'}


class QualityDimension(ContractModel):
    status: EvidenceStatus = EvidenceStatus.UNKNOWN
    score: float | None = Field(default=None, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class ShotQualityReport(ContractModel):
    shot_id: str
    scene_id: str
    artifact_id: str
    artifact_version: int = Field(ge=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    duration_seconds: float = Field(gt=0)
    profile: QualityProfile
    status: ShotQualityStatus
    dimensions: dict[str, QualityDimension]
    evaluation_ids: list[str]
    inspection_ids: list[str]
    blocking_issue_ids: list[str]
    human_review_items: list[str] = Field(default_factory=list)
    visual_score: float | None = None
    cinematic_score: float | None = None


class SceneQualityReport(ContractModel):
    scene_id: str
    shot_ids: list[str]
    accepted_seconds: float
    blocking_issue_ids: list[str]


class FilmQualityReport(ContractModel):
    project_id: str
    profile: QualityProfile
    shots: list[ShotQualityReport]
    scenes: list[SceneQualityReport]
    accepted_seconds: float
    accepted_shots: int
    minimum_runtime_met: bool
    minimum_shots_met: bool
    human_aesthetically_approved: bool = False
    review_proxy_artifact_id: str | None = None
    cinematic_evaluation_id: str | None = None


def aggregate_shot(shot, artifact, evaluations, inspections, *, thresholds=None, audio_evidence=None, cinematic_evidence=None):
    thresholds = thresholds or QualityThresholds()
    digest = artifact.metadata.get('sha256')
    # Never import a score from an older or different media revision.
    ev = [e for e in evaluations if e.target_artifact_id == artifact.artifact_id
          and e.target_artifact_version == artifact.version]
    ins = [i for i in inspections if i.target_artifact_id == artifact.artifact_id
           and i.target_artifact_version == artifact.version and i.target_sha256 == digest]
    # History stays in immutable source artifacts. A completed review revision replaces
    # that profile group's current verdict; it must not inherit a superseded failure.
    ev=list({e.layer:e for e in ev}.values())
    ins=list({tuple(sorted(s.profile.value for s in i.scores)):i for i in ins}.values())
    dimensions = {k:QualityDimension() for k in (*VISUAL, *AUDIO, *CINEMATIC)}
    blocking = []
    for i in ins:
        for s in i.scores:
            key = PROFILE_MAP.get(s.profile.value)
            if key:
                dimensions[key] = QualityDimension(score=s.score, evidence_ids=[i.result_id],
                    status=EvidenceStatus.PASS if s.score >= thresholds.visual_minimum else EvidenceStatus.FAIL)
        for issue in i.issues:
            if issue.severity in {IssueSeverity.MAJOR, IssueSeverity.CRITICAL}:
                blocking.append(issue.issue_id)
                key = ISSUE_MAP.get(issue.issue_type.value, 'artifact_detection')
                dimensions[key] = QualityDimension(status=EvidenceStatus.FAIL, evidence_ids=[issue.issue_id])
    for e in ev:
        blocking.extend(i.issue_id for i in e.issues if i.severity in {IssueSeverity.MAJOR, IssueSeverity.CRITICAL})
    for name,fact in (cinematic_evidence or {}).items():
        if name not in CINEMATIC or not fact.evidence_ids:
            raise ValueError('cinematic dimension requires named source evidence')
        dimensions[name]=fact
    # Audio facts require their own evidence; VLM cannot hear the native track.
    for name, fact in (audio_evidence or {}).items():
        if name not in AUDIO or not fact.evidence_ids:
            raise ValueError('audio status requires named source evidence')
        dimensions[name] = fact
    pending = []
    if shot.narrative.dialogue or any(p.dialogue for p in shot.performances):
        for name in ('dialogue_intelligibility_status', 'speaker_identity_status', 'native_dialogue_overlap'):
            if dimensions[name].status == EvidenceStatus.UNKNOWN:
                dimensions[name].status = EvidenceStatus.HUMAN_REQUIRED
                pending.append(name)
    visual = [s.score for i in ins for s in i.scores]
    cinematic = [e for e in ev if e.layer == EvaluationLayer.CINEMATIC]
    technical = [e for e in ev if e.layer == EvaluationLayer.TECHNICAL_QC]
    vscore = min(visual) if visual else None
    cscore = min(e.score for e in cinematic) if cinematic else None
    passed = (bool(ins) and all(i.decision == VisionDecision.PASS for i in ins) and not blocking
              and vscore >= thresholds.visual_minimum and bool(cinematic)
              and all(e.passed for e in cinematic) and cscore >= thresholds.cinematic_minimum
              and bool(technical) and all(e.passed for e in technical)
              and not any(dimensions[name].status==EvidenceStatus.FAIL for name in AUDIO))
    status = ShotQualityStatus.ACCEPTED if passed else ShotQualityStatus.HUMAN_REVIEW
    if blocking and any(i.decision in {VisionDecision.REPAIR, VisionDecision.REGENERATE} for i in ins):
        status = ShotQualityStatus.REPAIRING
    return ShotQualityReport(shot_id=shot.shot_id, scene_id=shot.scene_id, artifact_id=artifact.artifact_id,
        artifact_version=artifact.version, sha256=digest, duration_seconds=shot.duration_seconds,
        profile=thresholds.profile, status=status, dimensions=dimensions, evaluation_ids=[e.evaluation_id for e in ev],
        inspection_ids=[i.result_id for i in ins], blocking_issue_ids=list(dict.fromkeys(blocking)),
        human_review_items=pending, visual_score=vscore, cinematic_score=cscore)


def aggregate_film(project_id, shots, thresholds=None):
    thresholds = thresholds or QualityThresholds()
    if len({s.shot_id for s in shots}) != len(shots):
        raise ValueError('film aggregation accepts one exact selected version per shot')
    accepted = [s for s in shots if s.status == ShotQualityStatus.ACCEPTED]
    scenes = [SceneQualityReport(scene_id=sid, shot_ids=[s.shot_id for s in shots if s.scene_id == sid],
        accepted_seconds=sum(s.duration_seconds for s in accepted if s.scene_id == sid),
        blocking_issue_ids=[i for s in shots if s.scene_id == sid for i in s.blocking_issue_ids])
        for sid in dict.fromkeys(s.scene_id for s in shots)]
    seconds = sum(s.duration_seconds for s in accepted)
    return FilmQualityReport(project_id=project_id, profile=thresholds.profile, shots=shots, scenes=scenes,
        accepted_seconds=seconds, accepted_shots=len(accepted), minimum_runtime_met=seconds >= thresholds.minimum_accepted_seconds,
        minimum_shots_met=len(accepted) >= thresholds.minimum_accepted_shots)
