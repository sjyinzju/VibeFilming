"""Validated uploads, typed bindings, draft adoption, and request integration."""

from __future__ import annotations

import asyncio
import io

import httpx
from PIL import Image

from movie_agent.api.app import create_app
from movie_agent.media import (
    ImageReferenceBindingInput,
    MediaReference,
    ReferenceBindingScope,
    ReferencePurpose,
    ReferenceResolver,
    ReferenceType,
)
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_contracts import sample_shot
from movie_agent.domain import Character, Location, Scene
from tests.test_p2a_api import service_at
from tests.test_p2a_runtime import brief


def image_bytes(format_name: str, *, size=(37, 23)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (45, 72, 91)).save(buffer, format=format_name)
    return buffer.getvalue()


def binding(**updates) -> dict:
    payload = {
        "reference_type": "style",
        "binding_scope": "project",
        "purpose": "visual_style",
    }
    payload.update(updates)
    return payload


def test_png_jpeg_webp_upload_validation_preview_and_provenance(tmp_path) -> None:
    async def scenario() -> None:
        service = service_at(tmp_path, FakeReasoningProvider())
        app = create_app(service)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            for format_name, mime_type, filename in (
                ("PNG", "image/png", "../../portrait.png"),
                ("JPEG", "image/jpeg", "portrait.jpg"),
                ("WEBP", "image/webp", "portrait.webp"),
            ):
                original = image_bytes(format_name)
                response = await client.post(
                    "/drafts/draft_upload123/image-references",
                    data={"binding": __import__("json").dumps(binding())},
                    files={"file": (filename, original, mime_type)},
                )
                assert response.status_code == 201, response.text
                result = response.json()
                assert result["artifact_uri"] == f"artifact://{result['artifact_id']}/v1"
                assert result["width"] == 37 and result["height"] == 23
                assert result["size_bytes"] == len(original)
                assert result["artifact"]["metadata"]["origin"] == "user_upload"
                assert result["artifact"]["provenance"]["tool"] == "user_upload"
                assert result["reference"]["binding_scope"] == "project"
                assert ".." not in result["reference"]["original_filename"]
                assert str(tmp_path).replace("\\", "/") not in response.text.replace("\\", "/")
                preview = await client.get(
                    f"/artifacts/{result['artifact_id']}/preview",
                    params={"draft_id": "draft_upload123"},
                )
                assert preview.status_code == 200 and preview.content == original
                thumbnail = await client.get(
                    f"/artifacts/{result['artifact_id']}/thumbnail",
                    params={"draft_id": "draft_upload123"},
                )
                assert thumbnail.status_code == 200 and thumbnail.headers["content-type"].startswith("image/")
                assert thumbnail.content != original

            invalid_mime = await client.post(
                "/drafts/draft_upload123/image-references",
                data={"binding": __import__("json").dumps(binding())},
                files={"file": ("payload.svg", b"<svg/>", "image/svg+xml")},
            )
            assert invalid_mime.status_code == 422
            corrupt = await client.post(
                "/drafts/draft_upload123/image-references",
                data={"binding": __import__("json").dumps(binding())},
                files={"file": ("fake.png", b"not a png", "image/png")},
            )
            assert corrupt.status_code == 422

            refs = (await client.get("/drafts/draft_upload123/image-references")).json()
            assert len(refs) == 3
            removed = await client.delete(
                f"/drafts/draft_upload123/image-references/{refs[0]['reference_id']}"
            )
            assert removed.status_code == 204
            assert len((await client.get("/drafts/draft_upload123/image-references")).json()) == 2
            # Unbinding does not physically delete the immutable original.
            assert (await client.get(
                f"/artifacts/{refs[0]['artifact_id']}/content",
                params={"draft_id": "draft_upload123"},
            )).status_code == 200

    asyncio.run(scenario())


def test_size_limit_mime_mismatch_and_binary_immutability(tmp_path) -> None:
    async def scenario() -> None:
        service = service_at(tmp_path, FakeReasoningProvider())
        service.reference_uploads.max_size_bytes = 64
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)),
                                    base_url="http://test") as client:
            too_large = await client.post(
                "/drafts/draft_limits123/image-references",
                data={"binding": __import__("json").dumps(binding())},
                files={"file": ("large.png", image_bytes("PNG"), "image/png")},
            )
            assert too_large.status_code == 413
        service.reference_uploads.max_size_bytes = 1024 * 1024
        result = service.reference_uploads.upload_draft(
            "draft_limits123", image_bytes("PNG"), filename="source.png", mime_type="image/png",
            binding=ImageReferenceBindingInput.model_validate(binding()),
        )
        artifacts, binaries = service.reference_uploads._draft_stores("draft_limits123")
        try:
            binaries.put(result.artifact_id, 1, b"replacement", mime_type="image/png", extension="png")
            raise AssertionError("immutable version was overwritten")
        except FileExistsError:
            pass
        assert artifacts.get(result.artifact_id).uri == result.artifact_uri
        try:
            service.reference_uploads.upload_draft(
                "draft_limits123", image_bytes("JPEG"), filename="wrong.png", mime_type="image/png",
                binding=ImageReferenceBindingInput.model_validate(binding()),
            )
            raise AssertionError("MIME mismatch was accepted")
        except ValueError as error:
            assert "MIME" in str(error)

    asyncio.run(scenario())


def test_reference_duplicate_precedence_draft_adoption_and_generation_requests(tmp_path) -> None:
    async def scenario() -> None:
        service = service_at(tmp_path, FakeReasoningProvider(), auto_approve=True)
        upload = service.reference_uploads.upload_draft(
            "draft_adoption123", image_bytes("PNG"), filename="style.png", mime_type="image/png",
            binding=ImageReferenceBindingInput.model_validate(binding()),
        )
        # Exact binding retries are idempotent inside one bank.
        bank = service.reference_uploads._draft_bank("draft_adoption123")
        assert bank.add(upload.reference).reference_id == upload.reference.reference_id
        record = service.create(brief(), draft_id="draft_adoption123")
        pid = record.project.project_id
        engine = service.engine(pid)
        assert engine.reference_bank.all()[0].artifact_id == upload.artifact_id
        assert engine.artifact_store.get(upload.artifact_id).uri == upload.artifact_uri
        assert upload.artifact_id in record.project.brief.reference_images

        shot = sample_shot().model_copy(update={"scene_id": "scene_1"})
        scene = __import__("movie_agent.domain", fromlist=["Scene"]).Scene(
            scene_id="scene_1", title="Scene", purpose="Test", location_id="location_1",
            character_ids=["character_1"], shot_ids=[shot.shot_id], time_description="day",
        )
        refs = [
            MediaReference(reference_type=ReferenceType.STYLE, artifact_id="project",
                           binding_scope=ReferenceBindingScope.PROJECT,
                           purpose=ReferencePurpose.VISUAL_STYLE, project_id=pid),
            MediaReference(reference_type=ReferenceType.CHARACTER, artifact_id="entity",
                           binding_scope=ReferenceBindingScope.ENTITY,
                           purpose=ReferencePurpose.CHARACTER_IDENTITY, project_id=pid,
                           entity_id="character_1"),
            MediaReference(reference_type=ReferenceType.LOCATION, artifact_id="scene",
                           binding_scope=ReferenceBindingScope.SCENE,
                           purpose=ReferencePurpose.SCENE_CONCEPT, project_id=pid,
                           scene_id="scene_1"),
            MediaReference(reference_type=ReferenceType.STYLE, artifact_id="shot",
                           binding_scope=ReferenceBindingScope.SHOT,
                           purpose=ReferencePurpose.SHOT_GUIDANCE, project_id=pid,
                           shot_id=shot.shot_id),
            MediaReference(reference_type=ReferenceType.STYLE, artifact_id="unrelated",
                           binding_scope=ReferenceBindingScope.SHOT,
                           purpose=ReferencePurpose.SHOT_GUIDANCE, project_id=pid,
                           shot_id="other"),
        ]
        resolved = ReferenceResolver().resolve(refs, project_id=pid, shot=shot, scene=scene)
        assert [item.artifact_id for item in resolved] == ["shot", "scene", "entity", "project"]

        service.start(pid)
        await service.tasks[pid]
        artifacts = engine.artifact_store.list_all()
        frame = next(item for item in artifacts if item.artifact_type.value == "frame")
        assert upload.artifact_id in frame.provenance.input_artifact_ids
        video = next(item for item in artifacts if item.artifact_type.value == "video")
        assert upload.artifact_id in video.provenance.input_artifact_ids
        assert upload.artifact_id in video.provenance.parameters["reference_artifact_ids"]

    asyncio.run(scenario())


def test_canonical_project_reference_command_persists_and_unbinds_without_deleting(tmp_path) -> None:
    async def scenario() -> None:
        service = service_at(tmp_path, FakeReasoningProvider())
        pid = service.create(brief()).project.project_id
        app = create_app(service)
        original = image_bytes("PNG")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            uploaded = await client.post(
                f"/projects/{pid}/image-references",
                data={"binding": __import__("json").dumps(binding())},
                files={"file": ("project.png", original, "image/png")},
            )
            assert uploaded.status_code == 201, uploaded.text
            result = uploaded.json()
            snapshot = (await client.get(f"/projects/{pid}/studio")).json()
            assert snapshot["media_references"][0]["reference_id"] == result["reference"]["reference_id"]
            assert snapshot["project"]["brief"]["reference_images"] == []  # frozen Brief is unchanged
            removed = await client.delete(
                f"/projects/{pid}/image-references/{result['reference']['reference_id']}"
            )
            assert removed.status_code == 204
            assert (await client.get(f"/projects/{pid}/studio")).json()["media_references"] == []
            assert (await client.get(
                f"/artifacts/{result['artifact_id']}/content", params={"project_id": pid}
            )).content == original

        restarted = service_at(tmp_path, FakeReasoningProvider())
        assert restarted.engine(pid).reference_bank.all() == []

    asyncio.run(scenario())


def test_entity_scene_and_shot_binding_commands_validate_canonical_targets(tmp_path) -> None:
    service = service_at(tmp_path, FakeReasoningProvider())
    pid = service.create(brief()).project.project_id
    engine = service.engine(pid)
    shot = sample_shot().model_copy(update={"scene_id": "scene_reference"}, deep=True)
    engine.current_project.characters = [Character(character_id="character_reference", name="Mara")]
    engine.current_project.locations = [Location(location_id="location_reference", name="Archive")]
    engine.current_project.scenes = [Scene(
        scene_id="scene_reference", title="Archive", purpose="Test references",
        location_id="location_reference", time_description="night",
        character_ids=["character_reference"], shot_ids=[shot.shot_id],
    )]
    engine.current_project.shots = [shot]
    for scope, target, reference_type, purpose in (
        ("entity", {"entity_id": "character_reference"}, "character", "character_identity"),
        ("scene", {"scene_id": "scene_reference"}, "location", "scene_concept"),
        ("shot", {"shot_id": shot.shot_id}, "style", "shot_guidance"),
    ):
        service.add_image_reference(
            pid, image_bytes("PNG"), filename=f"{scope}.png", mime_type="image/png",
            binding=ImageReferenceBindingInput(
                reference_type=reference_type, binding_scope=scope, purpose=purpose, **target,
            ),
        )
    assert [item.binding_scope.value for item in engine.reference_bank.all()] == [
        "entity", "scene", "shot",
    ]
    restarted = service_at(tmp_path, FakeReasoningProvider())
    assert len(restarted.engine(pid).reference_bank.all()) == 3
