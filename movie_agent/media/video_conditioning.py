"""Explicit reference-to-boundary compilation proof for frame-conditioned video providers."""
from movie_agent.media.contracts import MediaReference, ReferenceType as R, VideoReferenceConditioning
from movie_agent.quality.budget import fingerprint


def reference_fingerprint(references):
    return fingerprint([r.model_dump(mode='json',exclude={'reference_id'}) for r in references])


def exact(references):
    return [(r.artifact_id,r.version,r.sha256) for r in references]


def bind_reviewed_boundaries(request,store):
    if request.mode.value not in {'first_frame_to_video','first_last_frame_to_video'}:return request
    references=[r for r in request.references if r.reference_type not in {R.FIRST_FRAME,R.LAST_FRAME}]
    if not references:return request
    gate=store.get('frame_gate_'+request.shot_id)
    if gate is None:raise ValueError('Reference-conditioned video requires an exact frame gate')
    trace=VideoReferenceConditioning(reference_assets=references,
        boundary_frames=[r for r in (request.first_frame,request.last_frame) if r is not None],
        frame_gate=MediaReference(reference_type=R.SOURCE_IMAGE,artifact_id=gate.artifact_id,
            version=gate.version,sha256=gate.metadata['sha256']),reference_fingerprint=reference_fingerprint(references))
    bound=request.model_copy(update={'references':trace.boundary_frames,'reference_conditioning':trace})
    validate_reviewed_boundaries(bound,store)
    return bound


def validate_reviewed_boundaries(request,store):
    trace=request.reference_conditioning
    if trace is None:return
    for reference in [trace.frame_gate,*trace.boundary_frames,*trace.reference_assets]:
        artifact=store.get(reference.artifact_id,reference.version)
        if artifact is None or artifact.metadata.get('sha256')!=reference.sha256:
            raise ValueError('Video reference conditioning has an unavailable exact input')
    gate=store.read_structured(store.get(trace.frame_gate.artifact_id,trace.frame_gate.version))
    digest=reference_fingerprint(trace.reference_assets)
    if not gate.get('passed') or gate.get('reference_fingerprint')!=digest or trace.reference_fingerprint!=digest:
        raise ValueError('Reference selection changed or lacks reviewed boundary evidence; regenerate/review frames first')
    frames=[MediaReference.model_validate(r) for r in gate['frames']]
    actual=[r for r in (request.first_frame,request.last_frame) if r is not None]
    if exact(frames)!=exact(trace.boundary_frames) or exact(actual)!=exact(trace.boundary_frames):
        raise ValueError('Video boundaries differ from reviewed reference compilation')
    if any(r.reference_type not in {R.FIRST_FRAME,R.LAST_FRAME} for r in request.references):
        raise ValueError('Boundary-conditioned video must not silently dispatch unbound reference images')
