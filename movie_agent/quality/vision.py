"""Core validation and deterministic policy for model proposals (no I/O)."""

from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator

from movie_agent.domain import IssueSeverity, Provenance
from movie_agent.media.contracts import (
    FrameReference, MediaIssue, MediaIssueType as I, MediaRepairActionType as A,
    VisionDecision as D, VisionInspectionRequest, VisionInspectionResult,
    VisionScore, TimeRange, VisionInspectionProfile,
)

PROMPT_VERSION = "p4b.2"
SCHEMA_VERSION = "p4b.1"

# Reuse canonical field definitions; only Core-owned identity/version fields are
# absent at this untrusted proposal boundary. This is not a second result contract.
def _draft_type(name, canonical, excluded, overrides=None):
    fields = {key: (field.annotation, field) for key, field in canonical.model_fields.items()
              if key not in excluded}
    fields.update(overrides or {})
    return create_model(name, __config__=ConfigDict(extra="forbid", allow_inf_nan=False), **fields)


DraftFrameReference = _draft_type("DraftFrameReference", FrameReference, {"schema_version", "artifact_id"})
DraftTimeRange = _draft_type("DraftTimeRange", TimeRange, {"schema_version"})
DraftScore = _draft_type("DraftScore", VisionScore, {"schema_version"})
DraftIssue = _draft_type("DraftIssue", MediaIssue, {"schema_version", "issue_id"}, {
    "frame_references": (list[DraftFrameReference], Field(default_factory=list)),
    "time_ranges": (list[DraftTimeRange], Field(default_factory=list)),
})


class VisionInspectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    scores: list[DraftScore]
    issues: list[DraftIssue] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    proposed_decision: D
    summary: str


SUPPORTED_VIDEO_REPAIRS = frozenset({A.REWRITE_PROMPT, A.REGENERATE_VIDEO, A.CHANGE_CAMERA_CONTROL,
                                     A.REGENERATE_FIRST_FRAME, A.REGENERATE_LAST_FRAME})
ACTION_ISSUES = {
    A.CHANGE_CAMERA_CONTROL: {I.CAMERA_MOTION_MISMATCH, I.MOTION_FAILURE},
    A.REGENERATE_FIRST_FRAME: {I.FIRST_FRAME_MISMATCH, I.CONTINUITY_ERROR, I.IDENTITY_DRIFT,
                             I.SCENE_DRIFT, I.STYLE_MISMATCH, I.LIGHTING_MISMATCH},
    A.REGENERATE_LAST_FRAME: {I.LAST_FRAME_MISMATCH, I.ACTION_INCOMPLETE, I.CONTINUITY_ERROR},
}
BLOCKING = {IssueSeverity.MAJOR, IssueSeverity.CRITICAL}


def inspection_fingerprint(request: VisionInspectionRequest) -> str:
    """Independent of transient request/job IDs; revision deliberately busts cache."""
    value = {"target": request.video_artifact_id or request.image_artifact_id,
             "version": request.target_artifact_version, "sha256": request.target_sha256,
             "profiles": sorted(p.value for p in request.profiles),
             "schema": request.critic_schema_version, "prompt": PROMPT_VERSION,
             "critic_configuration": request.critic_configuration_fingerprint,
             "revision": request.inspection_revision, "sampling": request.sampling_policy,
             "ranges": [r.model_dump(mode="json") for r in request.targeted_time_ranges],
             "expected": request.expected_shot.model_dump(mode="json") if request.expected_shot else None,
             "requirements": request.expected_requirements, "language": request.output_language,
             "references": sorted((r.artifact_id, r.version, r.sha256) for r in request.reference_assets)}
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def supported_repair_actions(capabilities):
    """Only actions executable through the configured media providers are repairable."""
    actions = {A.REWRITE_PROMPT, A.REGENERATE_VIDEO, A.CHANGE_CAMERA_CONTROL} if any(c.video for c in capabilities) else set()
    if any(c.image and (c.image.image_to_image or c.image.text_to_image) for c in capabilities):
        actions.update({A.REGENERATE_FIRST_FRAME, A.REGENERATE_LAST_FRAME})
    return actions


def critic_configuration_fingerprint(provider, supported_actions=None):
    policy=getattr(provider, 'policy', VisionDecisionPolicy())
    settings=getattr(provider, 'settings', None)
    value={'provider_id':provider.provider_id, 'model':getattr(settings,'vision_model',None),
           'policy':policy.model_dump(mode='json'),
           'sampling_frames':[getattr(settings,'vision_fast_frames',None),getattr(settings,'vision_full_frames',None)],
           'video_transport':getattr(settings,'vision_video_transport',None),
           'supported_repairs':sorted(supported_actions) if supported_actions is not None else None}
    return sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def validate_semantics(request: VisionInspectionRequest, draft: VisionInspectionDraft) -> None:
    profiles = [s.profile for s in draft.scores]
    if len(set(profiles)) != len(profiles) or not set(profiles) <= set(request.profiles):
        raise ValueError("scores must be unique and belong to requested profiles")
    for issue in draft.issues:
        if issue.severity in BLOCKING and not any(e.strip() for e in issue.evidence):
            raise ValueError("major/critical issue requires observable evidence")
        if issue.suggested_action in ACTION_ISSUES and issue.issue_type not in ACTION_ISSUES[issue.suggested_action]:
            raise ValueError("issue type and repair action conflict")
        for time_range in issue.time_ranges:
            if (request.source_duration_seconds is None or
                not 0 <= time_range.start_seconds <= time_range.end_seconds <= request.source_duration_seconds):
                raise ValueError("issue time range outside source duration")
        for frame in issue.frame_references:
            if frame.frame_number is None and frame.timestamp_seconds is None:
                raise ValueError("frame evidence requires a frame number or timestamp")
            if frame.timestamp_seconds is not None and (
                request.source_duration_seconds is None or frame.timestamp_seconds > request.source_duration_seconds
            ):
                raise ValueError("frame timestamp outside source duration")
            if frame.frame_number is not None and (
                request.source_frame_count is None or frame.frame_number >= request.source_frame_count
            ):
                raise ValueError("frame number outside source frame count")
    if draft.proposed_decision == D.PASS and any(i.severity in BLOCKING for i in draft.issues):
        raise ValueError("proposed PASS contradicts unresolved major/critical issue")


class VisionDecisionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    minimum_score: float = Field(default=0.80, ge=0, le=1)
    profile_thresholds: dict[VisionInspectionProfile, float] = Field(default_factory=dict)

    @field_validator('profile_thresholds')
    @classmethod
    def valid_thresholds(cls, thresholds):
        if any(not 0 <= value <= 1 for value in thresholds.values()):
            raise ValueError('profile thresholds must be in 0..1')
        return thresholds

    def decide(self, request, draft, supported_actions=SUPPORTED_VIDEO_REPAIRS):
        validate_semantics(request, draft)
        if (set(s.profile for s in draft.scores) != set(request.profiles)
            or not any(e.strip() for e in draft.evidence)
            or draft.proposed_decision == D.HUMAN_REVIEW):
            return D.HUMAN_REVIEW, ["insufficient_evidence_or_uncertain_diagnosis"]
        blocking = [i for i in draft.issues if i.severity in BLOCKING]
        scores_ok = all(s.score >= self.profile_thresholds.get(s.profile, self.minimum_score) for s in draft.scores)
        if not blocking and scores_ok:
            return D.PASS, ["requested_profiles_passed_without_blocking_issues"]
        if not blocking:
            return D.HUMAN_REVIEW, ["low_scores_without_actionable_diagnosis"]
        actions = {i.suggested_action for i in blocking}
        if None in actions or not actions <= set(supported_actions):
            return D.HUMAN_REVIEW, ["unsupported_or_unspecified_repair"]
        if request.retry_count >= request.retry_budget:
            return D.HUMAN_REVIEW, ["automatic_retry_budget_exhausted"]
        if A.REGENERATE_VIDEO in actions:
            return D.REGENERATE, ["new_media_generation_required"]
        return D.REPAIR, ["targeted_media_repair_available"]

    def commit(self, request, draft, *, provider_id, model=None, metadata=None,
               supported_actions=SUPPORTED_VIDEO_REPAIRS):
        if request.target_artifact_version is None or request.target_sha256 is None:
            raise ValueError("Core must pin target version/hash before inspection")
        decision, reasons = self.decide(request, draft, supported_actions)
        target = request.video_artifact_id or request.image_artifact_id
        issues = []
        for issue in draft.issues:
            payload = issue.model_dump(mode="json")
            payload["frame_references"] = [dict(f, artifact_id=target) for f in payload["frame_references"]]
            issues.append(MediaIssue.model_validate(payload))
        fingerprint = inspection_fingerprint(request)
        parameters = {**(metadata or {}), "model": model, "prompt_version": PROMPT_VERSION,
                      "critic_schema_version": request.critic_schema_version,
                      "target_artifact_id": target, "target_artifact_version": request.target_artifact_version,
                      "target_sha256": request.target_sha256, "inspection_fingerprint": fingerprint,
                      "request_id": request.request_id,
                      "input_artifact_uris": [f"artifact://{target}/v{request.target_artifact_version}",
                                              *[r.artifact_uri for r in request.reference_assets]]}
        return VisionInspectionResult(request_id=request.request_id, target_artifact_id=target,
            target_artifact_version=request.target_artifact_version, target_sha256=request.target_sha256,
            project_id=request.project_id, scene_id=request.scene_id, shot_id=request.shot_id,
            inspection_fingerprint=fingerprint, scores=[VisionScore.model_validate(s.model_dump()) for s in draft.scores],
            issues=issues, evidence=draft.evidence, proposed_decision=draft.proposed_decision,
            decision=decision, decision_reasons=reasons, summary=draft.summary, provider_id=provider_id,
            model_service_id="vlm" if provider_id == "qwen3_vl" else None,
            provider_metadata=parameters, provenance=Provenance(role="Critic", tool="vision_adjudicator",
                provider_id=provider_id, model_service_id="vlm" if provider_id == "qwen3_vl" else None,
                project_id=request.project_id, scene_id=request.scene_id, shot_id=request.shot_id,
                input_artifact_ids=[target, *[r.artifact_id for r in request.reference_assets]], parameters=parameters))


def draft_from_result(result: VisionInspectionResult) -> VisionInspectionDraft:
    """All providers, including fakes, pass the same Core validation boundary."""
    return VisionInspectionDraft.model_validate({
        "scores": [{"profile": s.profile, "score": s.score} for s in result.scores],
        "issues": [i.model_dump(exclude={"schema_version", "issue_id", "time_ranges", "frame_references"}) | {
            "time_ranges": [t.model_dump(exclude={"schema_version"}) for t in i.time_ranges],
            "frame_references": [f.model_dump(exclude={"schema_version", "artifact_id"}) for f in i.frame_references],
        } for i in result.issues],
        "evidence": result.evidence, "proposed_decision": result.proposed_decision or result.decision,
        "summary": result.summary,
    })
