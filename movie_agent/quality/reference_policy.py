"""Role-scoped reference usability, distinct from final-shot SHOWCASE acceptance."""
from movie_agent.domain import IssueSeverity
from movie_agent.media import ReferenceType, VisionInspectionProfile as VP
from movie_agent.quality.vision import VisionDecisionPolicy

REVISION = 'p5r-reference-usability-1'
CRITIC_REVISION = 'p5r-observable-core-evidence-3'
AUTHORIZATION = 'User P5R sections 8, 9, 11, 24; same project and immutable history'
HARD = {
    ReferenceType.CHARACTER: {'wrong_identity', 'wrong_person', 'face_corruption', 'defining_feature_missing', 'unusable'},
    ReferenceType.LOCATION: {'wrong_location', 'spatial_structure', 'large_prohibited_object', 'unusable'},
    ReferenceType.PROP: {'wrong_object', 'core_shape', 'defining_design', 'unusable'},
    ReferenceType.STYLE: {'palette_language', 'render_language', 'style_contamination', 'unusable'},
}
SOFT = {'glow_intensity', 'minor_lighting', 'minor_color', 'small_wardrobe_detail',
        'minor_material', 'minor_clutter', 'minor_composition', 'minor_palette'}
ROLE_THRESHOLDS = {
    ReferenceType.CHARACTER: (.65, .80, .70),
    ReferenceType.LOCATION: (.60, .75, .65),
    ReferenceType.PROP: (.60, .75, .65),
    ReferenceType.STYLE: (.60, .70, .60),
}


def scoped_reference_requirements(role, target_description):
    """Deliberately accepts no project/global context at this boundary."""
    role = ReferenceType(role)
    if role not in HARD:
        raise ValueError('Unsupported reference role')
    return [target_description,
        f'Initial {role.value} reference usability, not a final film shot. An assigned name is a label, not an unseen face. '
        'Only the target description above is binding. Other characters, locations and unrelated props are NOT required. '
        'Judge prompt_alignment against CORE usability, not perfection of decorative details. '
        'Hard aspects: '+', '.join(sorted(HARD[role]))+'. Soft warning aspects: '+', '.join(sorted(SOFT))+'. '
        'A style anchor establishes palette, render language, contrast, texture, light and mood, not all scene facts. '
        'For each issue set reference_aspect to a listed aspect. Major/critical must identify observed_subject, '
        'observed_mismatch, blocking_reason for THIS role, core_usability_affected=true, and specific evidence. '
        'Soft details such as prop glow intensity cannot alone be major/critical. No invented defects or duplicate issues.']


def calibrated_policy(request):
    return (request.quality_policy_revision or '').startswith(('p5r-','reference-usability-','shot-usability-'))


def validate_core_evidence(request, draft):
    if not calibrated_policy(request):
        return
    if set(s.profile for s in draft.scores)!=set(request.profiles) or not any(e.strip() for e in draft.evidence):
        raise ValueError('Calibrated critic requires every requested score and observable top-level evidence')
    for issue in draft.issues:
        if request.reference_role:
            allowed = HARD[request.reference_role] | SOFT
            if issue.reference_aspect not in allowed:
                raise ValueError('reference issue requires a role-relevant structured reference_aspect')
        if issue.severity in {IssueSeverity.MAJOR, IssueSeverity.CRITICAL}:
            if request.reference_role and issue.reference_aspect in SOFT:
                raise ValueError('A soft reference detail cannot be major/critical; preserve it as a minor warning')
            if (request.reference_role==ReferenceType.PROP and issue.issue_type.value=='lighting_mismatch'
                and issue.reference_aspect!='unusable'):
                raise ValueError('Prop illumination is a soft warning, not a core-shape/design blocker')
            if issue.core_usability_affected is not True:
                raise ValueError('Major/critical must directly affect core reference or shot usability')
            for text in (issue.observed_subject, issue.observed_mismatch, issue.blocking_reason):
                if not text or len(text.strip()) < 4:
                    raise ValueError('Blocking evidence requires concrete subject, mismatch and role-specific reason')
                if text.strip().lower() in {'可能不符合', '看起来有差异', 'may not match', 'looks different', 'unknown', 'n/a'}:
                    raise ValueError('Speculation is not observable blocking evidence')


class ReferenceAcceptancePolicy(VisionDecisionPolicy):
    """No blanket threshold lowering: role-specific usability and unconditional hard blockers."""
    revision: str = REVISION

    def decide(self, request, draft, supported_actions=None):
        from movie_agent.quality.vision import SUPPORTED_VIDEO_REPAIRS
        if request.reference_role not in ROLE_THRESHOLDS:
            raise ValueError('ReferenceAcceptancePolicy requires an explicit reference role')
        thresholds = dict(zip((VP.IMAGE_QUALITY, VP.ARTIFACT_DETECTION, VP.PROMPT_ALIGNMENT),
                              ROLE_THRESHOLDS[request.reference_role]))
        policy = VisionDecisionPolicy(profile_thresholds=thresholds)
        decision, reasons = policy.decide(request, draft,
            supported_actions if supported_actions is not None else SUPPORTED_VIDEO_REPAIRS)
        return decision, [self.revision, *reasons]
