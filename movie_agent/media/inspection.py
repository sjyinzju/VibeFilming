"""Pin inspection inputs at the application/media boundary, before dispatch."""

from hashlib import sha256
from movie_agent.media.contracts import ReferenceType, VisionInspectionRequest
from movie_agent.media.storage import parse_artifact_uri


def media_hash(binaries, artifact, *, max_size=256 * 1024**2):
    if binaries.size(artifact.uri) > max_size:
        raise ValueError("inspection media exceeds size limit")
    digest = sha256()
    with binaries.open(artifact.uri) as source:
        for chunk in iter(lambda: source.read(1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pin_inspection(request: VisionInspectionRequest, artifacts, binaries):
    identity = request.video_artifact_id or request.image_artifact_id
    # Legacy requests resolve exactly once. Provider receives a pinned version.
    target = artifacts.get(identity, request.target_artifact_version)
    if target is None:
        raise ValueError("inspection target version unavailable")
    if parse_artifact_uri(target.uri) != (target.artifact_id, target.version):
        raise ValueError("inspection target URI does not address its version")
    digest = media_hash(binaries, target)
    if request.target_sha256 and request.target_sha256 != digest:
        raise ValueError("inspection target SHA mismatch")
    refs = []
    inputs = target.provenance.parameters.get("input_artifact_versions", []) or target.provenance.parameters.get("video_preflight_input_artifacts", [])
    frozen = {i["artifact_id"]: i for i in inputs if isinstance(i, dict) and "artifact_id" in i}
    for ref in request.reference_assets:
        version = ref.version
        bound = frozen.get(ref.artifact_id)
        if bound:
            if version is not None and version != bound["version"] and ref.reference_type in {ReferenceType.FIRST_FRAME, ReferenceType.LAST_FRAME}:
                raise ValueError("boundary reference differs from generation input version")
            version = bound["version"]
        versions = artifacts.list_versions(ref.artifact_id)
        if version is None:
            if ref.reference_type in {ReferenceType.FIRST_FRAME, ReferenceType.LAST_FRAME} and len(versions) != 1:
                raise ValueError("legacy video has ambiguous boundary frame version")
            resolved = artifacts.get(ref.artifact_id)
        else:
            resolved = artifacts.get(ref.artifact_id, version)
        if resolved is None or resolved.metadata.get("mime_type") not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("inspection reference unavailable or not an image")
        ref_hash = media_hash(binaries, resolved)
        if ref.sha256 and ref.sha256 != ref_hash:
            raise ValueError("inspection reference SHA mismatch")
        refs.append(ref.model_copy(update={"version": resolved.version, "sha256": ref_hash}))
    return request.model_copy(update={"target_artifact_version": target.version, "target_sha256": digest,
        "source_duration_seconds": target.metadata.get("duration_seconds"),
        "source_frame_count": target.metadata.get("frame_count"), "reference_assets": refs})
