"""Reviewed, immutable identity packs and deterministic shot-scoped selection."""
from enum import StrEnum

from pydantic import Field, model_validator

from movie_agent.domain import ContractModel
from movie_agent.media import MediaReference, ReferenceType, VisionDecision


class ReferenceRole(StrEnum):
    FACE_IDENTITY = 'face_identity'
    PORTRAIT = 'portrait'
    FULL_BODY = 'full_body'
    PROFILE = 'profile'
    SILHOUETTE = 'silhouette'
    WARDROBE = 'wardrobe'
    CONTEXTUAL = 'contextual'
    ENVIRONMENT = 'environment'
    LIGHTING = 'lighting_palette'
    LANDMARK = 'landmark'
    PROP = 'prop'
    STYLE = 'style'


class IdentityReference(ContractModel):
    artifact_id: str
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    reference_type: ReferenceType
    reference_role: ReferenceRole
    subject_id: str
    selected: bool = False
    quality_status: VisionDecision = VisionDecision.HUMAN_REVIEW
    inspection_result_id: str | None = None
    human_review_id: str | None = None
    acceptance_artifact_id: str | None = None
    known_limitations: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0, le=1)
    created_from: list[MediaReference] = Field(default_factory=list)

    @model_validator(mode='after')
    def reviewed_selection(self):
        if self.reference_type not in {ReferenceType.CHARACTER, ReferenceType.LOCATION,
                                        ReferenceType.PROP, ReferenceType.STYLE}:
            raise ValueError('identity pack supports character/location/prop/style only')
        human_accepted=bool(self.human_review_id and self.acceptance_artifact_id)
        if self.selected and not human_accepted and (self.quality_status != VisionDecision.PASS or not self.inspection_result_id):
            raise ValueError('selected anchor requires a passing baseline inspection')
        if any(r.version is None or r.sha256 is None for r in self.created_from):
            raise ValueError('reference ancestry requires exact versions and hashes')
        return self

    def media_reference(self):
        return MediaReference(reference_type=self.reference_type, artifact_id=self.artifact_id,
            version=self.version, sha256=self.sha256, entity_id=self.subject_id, selected=self.selected,
            semantic_role=self.reference_role.value,human_acceptance_artifact_id=self.acceptance_artifact_id,
            accepted_limitations=self.known_limitations)


class ReferenceIdentitySet(ContractModel):
    references: list[IdentityReference] = Field(default_factory=list)
    canonical_constraints: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_roles(self):
        keys = [(r.reference_type, r.subject_id, r.reference_role) for r in self.references if r.selected]
        if len(set(keys)) != len(keys):
            raise ValueError('a subject role must have exactly one selected version')
        return self


class ResolvedReferenceSet(ContractModel):
    shot_id: str
    references: list[MediaReference]
    reasons: list[str]
    canonical_constraints: list[str]
    hard_required_refs: list[MediaReference] = Field(default_factory=list)
    soft_preferred_refs: list[MediaReference] = Field(default_factory=list)
    missing_soft_refs: list[str] = Field(default_factory=list)


def hard_reference_subjects(shot, scene):
    visible = {cid for cid, state in shot.state_before.character_states.items() if state.visible}
    visible |= {cid for cid, state in shot.expected_state_after.character_states.items() if state.visible}
    visible |= {p.character_id for p in shot.performances if p.character_id not in shot.state_before.character_states}
    props = {pid for states in (shot.state_before.prop_states, shot.expected_state_after.prop_states)
             for pid, state in states.items() if state.visible}
    props |= {pid for states in (shot.state_before.character_states, shot.expected_state_after.character_states)
              for state in states.values() if state.visible for pid in state.held_prop_ids}
    return [*( (ReferenceType.CHARACTER,cid) for cid in sorted(visible)),
            (ReferenceType.LOCATION,scene.location_id),*((ReferenceType.PROP,pid) for pid in sorted(props))]


class ReferenceCompositionPolicy(ContractModel):
    roles: dict[ReferenceType,list[ReferenceRole]] = Field(default_factory=lambda: {
        ReferenceType.CHARACTER:[ReferenceRole.FACE_IDENTITY,ReferenceRole.PORTRAIT,ReferenceRole.WARDROBE,
            ReferenceRole.FULL_BODY,ReferenceRole.SILHOUETTE,ReferenceRole.PROFILE,ReferenceRole.CONTEXTUAL],
        ReferenceType.LOCATION:[ReferenceRole.ENVIRONMENT,ReferenceRole.LANDMARK,ReferenceRole.LIGHTING],
        ReferenceType.PROP:[ReferenceRole.PROP],ReferenceType.STYLE:[ReferenceRole.STYLE]})


class ReferenceResolver:
    """Compose distinct exact references by semantic role; never silently drop hard conditioning."""
    def __init__(self, *, max_references=6, style_limit=1, composition_policy=None):
        if max_references < 1 or style_limit < 0:raise ValueError('invalid reference bound')
        self.max_references,self.style_limit=max_references,style_limit
        self.composition_policy=composition_policy or ReferenceCompositionPolicy()

    def resolve(self, shot, scene, visual_bible, pack, *, store=None, repair_context=None):
        selected=[r for r in pack.references if r.selected]
        result,reasons,hard,soft,missing=[],[],[],[],[]
        seen={}
        def append_subject(kind,subject,required):
            roles=self.composition_policy.roles.get(kind,[])
            choices=[r for r in selected if r.reference_type==kind and r.subject_id==subject and r.reference_role in roles]
            choices.sort(key=lambda r:(roles.index(r.reference_role),r.artifact_id,r.version))
            if not choices:
                if required:raise ValueError(f'missing reviewed {kind.value} reference for {subject}')
                missing.append(f'{kind.value}:{subject}');return
            for ref in choices:
                key=(ref.subject_id,ref.artifact_id,ref.version,ref.sha256,ref.acceptance_artifact_id,ref.human_review_id)
                if store:
                    a=store.get(ref.artifact_id,ref.version)
                    if a is None or a.metadata.get('sha256')!=ref.sha256:raise ValueError('selected reference version/hash unavailable')
                    if ref.human_review_id:
                        record=store.get(ref.acceptance_artifact_id)
                        accepted=store.read_structured(record) if record else {}
                        review=accepted.get('human_review',{})
                        matches=any(b['subject_id']==subject and b['role']==ref.reference_role.value
                            and b['reference']['artifact_id']==ref.artifact_id and b['reference']['version']==ref.version
                            and b['reference']['sha256']==ref.sha256 for b in accepted.get('bindings',[]))
                        if review.get('review_id')!=ref.human_review_id or review.get('status')!='approved' or not matches:
                            raise ValueError('Human reference acceptance does not cover this exact role/version/hash')
                basis='human accepted with known limitations' if ref.human_review_id else 'VLM accepted'
                reasons.append(f'{kind.value} {subject}: {ref.reference_role.value} {ref.artifact_id}@v{ref.version}; {basis}')
                if key in seen:
                    # One image can establish several roles. Validate each role's approval
                    # above, then preserve its label without spending another image slot.
                    pin=seen[key]
                    pin.semantic_role += ' + '+ref.reference_role.value
                    pin.accepted_limitations=list(dict.fromkeys([*pin.accepted_limitations,*ref.known_limitations]))
                    continue
                if not required and len(result)>=self.max_references:
                    missing.append(f'{kind.value}:{subject}:reference_bound');continue
                pin=ref.media_reference();result.append(pin);(hard if required else soft).append(pin);seen[key]=pin
        for kind,subject in hard_reference_subjects(shot,scene):append_subject(kind,subject,True)
        if visual_bible and self.style_limit:append_subject(ReferenceType.STYLE,visual_bible.visual_bible_id,False)
        if len(result)>self.max_references:raise ValueError('required identities exceed reference bound; replan shot instead of truncating')
        if repair_context:reasons.append('Repair re-resolved canonical references for '+repair_context.target_artifact_id)
        return ResolvedReferenceSet(shot_id=shot.shot_id,references=result,reasons=reasons,
            canonical_constraints=[*pack.canonical_constraints,*(repair_context.preserve if repair_context else [])],
            hard_required_refs=hard,soft_preferred_refs=soft,missing_soft_refs=missing)
