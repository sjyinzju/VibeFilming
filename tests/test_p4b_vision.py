"""No-Spark tests for the real adapter's transport and Core proposal boundary."""

import asyncio
from hashlib import sha256
import io
import json
import sys
import time
import types

import httpx
import pytest
from pydantic import ValidationError

from movie_agent.artifacts import LocalArtifactStore
from movie_agent.domain import Artifact, ArtifactType, GenerationJob, IssueSeverity, ProviderErrorType, ResourceClass
from movie_agent.execution import JobManager, LocalEventBus
from movie_agent.media import (VisionInspectionRequest, VisionInspectionResult, VisionInspectionProfile as P,
    MediaIssueType as I, MediaRepairActionType as A, HumanRepairInput, HumanRepairDirective,
    MediaReference, ReferenceType)
from movie_agent.media.inspection import pin_inspection
from movie_agent.media.runtime import MediaRuntime
from movie_agent.media.storage import LocalBinaryArtifactStore
from movie_agent.media.transport import MediaReferenceBinaryResolver
from movie_agent.model_services.resources import ResourceRuntimeSettings
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.media import MockVisionProvider, _MOCK_MP4, _png
from movie_agent.providers.qwen3_vl import Qwen3VLVisionProvider
from movie_agent.providers.registry import MediaProviderSettings, ProviderFactory, ProviderRegistry
from movie_agent.providers.spark_media import SparkMediaStager, _REMOTE_STAGE
from movie_agent.quality.vision import VisionInspectionDraft, VisionDecisionPolicy, validate_semantics, inspection_fingerprint


def request(**changes):
    return VisionInspectionRequest(job_id="inspect:shot:v1", project_id="project", shot_id="shot",
        video_artifact_id="video", target_artifact_version=1, target_sha256="a" * 64,
        source_duration_seconds=15, source_frame_count=360, profiles=[P.VIDEO_QUALITY],
        output_artifact_id="inspection", **changes)


def draft(**changes):
    return VisionInspectionDraft.model_validate({"scores": [{"profile": "video_quality", "score": .94}],
        "issues": [], "evidence": ["A person raises a cup in a kitchen; the camera remains static."],
        "summary": "The action completes.", "proposed_decision": "pass", **changes})


def issue(**changes):
    return {"issue_type": "camera_motion_mismatch", "severity": "major", "message": "Camera remains still",
        "evidence": ["The subject moves but the background perspective does not change."],
        "time_ranges": [{"start_seconds": 4.2, "end_seconds": 7.1}],
        "suggested_action": "change_camera_control", **changes}


async def fake_probe(content):
    return {'duration_seconds':15,'fps':24,'frame_count':360,'width':1024,'height':576}


@pytest.mark.parametrize("bad", [
    {"scores": [{"profile": "camera_motion", "score": .9}]},
    {"scores": [{"profile": "video_quality", "score": .9}] * 2},
    {"issues": [issue(evidence=[])]},
    {"issues": [issue(time_ranges=[{"start_seconds": 9, "end_seconds": 16}])]},
    {"issues": [issue(time_ranges=[{"start_seconds": 8, "end_seconds": 3}])]},
    {"issues": [issue(frame_references=[{"timestamp_seconds": 16}])]},
    {"issues": [issue(frame_references=[{"frame_number": 360}])]},
    {"issues": [issue(frame_references=[{}])]},
    {"issues": [issue(issue_type="identity_drift")]},
])
def test_semantic_rejections(bad):
    with pytest.raises(ValueError):
        validate_semantics(request(), draft(proposed_decision="repair", **bad))


@pytest.mark.parametrize("score", [-.01, 1.01, float('nan')])
def test_score_bounds(score):
    with pytest.raises(ValueError):
        draft(scores=[{"profile": "video_quality", "score": score}])


def test_pass_blockers_and_core_authority():
    with pytest.raises(ValueError):
        validate_semantics(request(), draft(issues=[issue(severity="critical")]))
    policy = VisionDecisionPolicy(minimum_score=.8)
    assert policy.decide(request(), draft())[0] == 'pass'
    assert policy.decide(request(), draft(proposed_decision='regenerate'))[0] == 'pass'
    assert policy.decide(request(), draft(scores=[]))[0] == 'human_review'
    assert policy.decide(request(), draft(evidence=[]))[0] == 'human_review'
    assert policy.decide(request(), draft(proposed_decision='human_review'))[0] == 'human_review'
    broken = draft(proposed_decision='repair', issues=[issue()])
    assert policy.decide(request(), broken)[0] == 'repair'
    assert policy.decide(request(retry_budget=0), broken)[0] == 'human_review'
    assert policy.decide(request(), broken, set())[0] == 'human_review'
    assert policy.decide(request(), draft(proposed_decision='repair', issues=[issue(suggested_action='regenerate_video')]))[0] == 'regenerate'


def test_draft_excludes_core_identity_and_committed_result():
    schema = VisionInspectionDraft.model_json_schema()
    assert set(schema['properties']) == {'scores','issues','evidence','summary','proposed_decision'}
    with pytest.raises(ValidationError):
        draft(provider_id='qwen3_vl')
    result = VisionDecisionPolicy().commit(request(), draft(), provider_id='qwen3_vl', model='movie-agent-vision')
    assert result.target_artifact_version == 1 and result.target_sha256 == 'a' * 64
    assert result.provenance.parameters['input_artifact_uris'] == ['artifact://video/v1']
    invalid = result.model_dump()
    invalid['issues'] = [issue(severity='critical')]
    with pytest.raises(ValueError):
        VisionInspectionResult.model_validate(invalid)
    assert inspection_fingerprint(request()) != inspection_fingerprint(request().model_copy(update={'target_artifact_version': 2}))
    assert inspection_fingerprint(request()) != inspection_fingerprint(request(inspection_revision=2))


def stores(tmp_path):
    artifacts, binaries = LocalArtifactStore(tmp_path / 'artifacts'), LocalBinaryArtifactStore(tmp_path / 'binary')
    for version in (1,2):
        data = _MOCK_MP4 + bytes([version])
        binary = binaries.put('video',version,data,mime_type='video/mp4',extension='mp4')
        artifacts.register(Artifact(artifact_id='video',artifact_type=ArtifactType.VIDEO,version=version,uri=binary.uri,
            metadata={'mime_type':'video/mp4','duration_seconds':15,'frame_count':360}))
    return artifacts,binaries


def test_exact_version_hash_and_evidence_resume(tmp_path):
    async def scenario():
        artifacts,binaries = stores(tmp_path)
        artifacts.select('video',2)
        req = request().model_copy(update={'target_sha256':None})
        pinned = pin_inspection(req,artifacts,binaries)
        assert pinned.target_artifact_version == 1
        assert pinned.target_sha256 == sha256(_MOCK_MP4 + b'\1').hexdigest()
        with pytest.raises(ValueError):
            pin_inspection(request(),artifacts,binaries)
        bus, registry = LocalEventBus(),ProviderRegistry()
        class Counting(MockVisionProvider):
            calls=0
            async def inspect(self, req):
                self.calls+=1
                return await super().inspect(req)
        provider=Counting(); registry.register(provider)
        job=GenerationJob(job_id=req.job_id,project_id='project',task='vision',idempotency_key=req.job_id,resource_class=ResourceClass.LIGHT)
        media=MediaRuntime(artifacts,binaries,registry,bus,'trace'); media.bind_jobs(JobManager(bus,'trace'))
        result=await media.inspect(job,req)
        evidence=artifacts.get('inspection')
        assert evidence.uri == 'artifact://inspection/v1'
        assert artifacts.read_structured(evidence)['target_artifact_version'] == 1
        restarted=MediaRuntime(LocalArtifactStore(tmp_path/'artifacts'),binaries,registry,bus,'trace')
        restarted.bind_jobs(JobManager(bus,'trace'))
        assert (await restarted.inspect(job,req)).result_id == result.result_id
        assert provider.calls == 1
        await restarted.inspect(job.model_copy(update={'job_id':'inspect:v2','idempotency_key':'inspect:v2'}),
            req.model_copy(update={'job_id':'inspect:v2','target_artifact_version':2}))
        assert provider.calls == 2 and len(artifacts.list_versions('inspection')) == 1
        assert len(restarted.artifact_store.list_versions('inspection')) == 2
    asyncio.run(scenario())


def test_remote_staging_dedup_ttl_capacity_and_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules,'fcntl',types.SimpleNamespace(flock=lambda *a:None,LOCK_EX=2))
    def stage(data, ttl=3600, capacity=30):
        digest=sha256(data).hexdigest()
        monkeypatch.setattr(sys,'argv',['stage',str(tmp_path),digest,'mp4',str(len(data)),str(ttl),str(capacity)])
        monkeypatch.setattr(sys,'stdin',types.SimpleNamespace(buffer=io.BytesIO(data)))
        out=io.StringIO(); monkeypatch.setattr(sys,'stdout',out)
        exec(compile(_REMOTE_STAGE,'remote-stage','exec'),{})
        return json.loads(out.getvalue())
    assert not stage(b'123')['deduplicated']
    assert stage(b'123')['deduplicated']
    unrelated=tmp_path/'user-file.mp4'; unrelated.write_bytes(b'unchanged')
    with pytest.raises(AssertionError): stage(b'x'*31)
    old=tmp_path/(sha256(b'123').hexdigest()+'.mp4')
    import os
    os.utime(old,(time.time()-7200,)*2)
    stage(b'new')
    assert not old.exists() and unrelated.read_bytes()==b'unchanged'


def test_stager_validation_and_transport_failure():
    async def scenario():
        settings=ResourceRuntimeSettings()
        with pytest.raises(ValueError): SparkMediaStager(settings,remote_root='/media/../etc')
        async def transfer(content,digest,ext): return {'sha256':digest,'size_bytes':len(content),'deduplicated':True}
        stage=SparkMediaStager(settings,runner=transfer)
        result=await stage.stage(_MOCK_MP4,'video/mp4')
        assert result.url == f'file:///media/{sha256(_MOCK_MP4).hexdigest()}.mp4'
        for data,mime in [(b'fake','video/mp4'),(_MOCK_MP4,'image/png'),(_png(),'text/plain')]:
            with pytest.raises(ProviderFailure): await stage.stage(data,mime)
        async def broken(*args): raise TimeoutError()
        with pytest.raises(ProviderFailure) as error: await SparkMediaStager(settings,runner=broken).stage(_png(),'image/png')
        assert error.value.error_type==ProviderErrorType.UNAVAILABLE
    asyncio.run(scenario())


@pytest.mark.parametrize('behavior',['valid','bad_json','bad_semantics','timeout','unavailable','http400'])
def test_real_adapter_structured_boundary_without_network(tmp_path,behavior):
    async def scenario():
        artifacts,binaries=stores(tmp_path)
        req=pin_inspection(request().model_copy(update={'target_sha256':None}),artifacts,binaries)
        calls=[]
        async def handler(http_request):
            if http_request.method=='GET':return httpx.Response(200)
            calls.append(json.loads(http_request.content))
            if behavior=='timeout':raise httpx.ReadTimeout('timeout')
            if behavior=='unavailable':raise httpx.ConnectError('unavailable')
            if behavior=='http400':return httpx.Response(400)
            content=draft().model_dump_json() if behavior=='valid' else ('bad json' if behavior=='bad_json' else draft(scores=[]).model_dump_json())
            return httpx.Response(200,json={'id':'chatcmpl-test','choices':[{'finish_reason':'stop','message':{'content':content}}]})
        async def transfer(content,digest,ext):return {'sha256':digest,'size_bytes':len(content),'deduplicated':False}
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider=Qwen3VLVisionProvider(settings=MediaProviderSettings(vision_video_transport='native'),resolver=MediaReferenceBinaryResolver(artifacts,binaries),
            stager=SparkMediaStager(ResourceRuntimeSettings(),runner=transfer),client=client,
            video_probe=fake_probe)
        assert await provider.health() and (await provider.capabilities()).requires_resource_lease
        if behavior in {'valid','bad_semantics'}:
            result=await provider.inspect(req)
            assert result.decision == ('pass' if behavior=='valid' else 'human_review')
            assert calls[0]['media_io_kwargs']=={'video':{'num_frames':24,'video_backend':'opencv'}}
            assert calls[0]['response_format']['json_schema']['schema']==VisionInspectionDraft.model_json_schema()
            assert 'E:\\' not in json.dumps(calls)
        else:
            with pytest.raises(ProviderFailure) as error:await provider.inspect(req)
            if behavior=='timeout': assert error.value.error_type==ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN
            if behavior=='unavailable':assert error.value.error_type==ProviderErrorType.MODEL_NOT_READY
            assert len(calls)==(2 if behavior=='bad_json' else 1)
        assert await provider.cancel(req.request_id) is False
        await client.aclose()
    asyncio.run(scenario())


def test_real_provider_registration_no_mock_fallback():
    settings=MediaProviderSettings(vision_provider='qwen3_vl')
    registry=ProviderFactory.defaults(settings=settings).build_registry(settings)
    assert registry.get('qwen3_vl').provider_id=='qwen3_vl'
    with pytest.raises(LookupError):registry.get('mock-vision')


def test_provider_switch_cannot_reuse_mock_inspection_or_job(tmp_path):
    async def scenario():
        artifacts,binaries=stores(tmp_path)
        bus=LocalEventBus();registry=ProviderRegistry();registry.register(MockVisionProvider())
        media=MediaRuntime(artifacts,binaries,registry,bus,'trace');media.bind_jobs(JobManager(bus,'trace'))
        req=request().model_copy(update={'target_sha256':None})
        active=GenerationJob(job_id=req.job_id,project_id='project',task='vision',idempotency_key=req.job_id,resource_class=ResourceClass.LIGHT)
        old=await media.inspect(active,req)
        class Other(MockVisionProvider):provider_id='explicit-fake-vision'
        other=ProviderRegistry();other.register(Other())
        switched=MediaRuntime(artifacts,binaries,other,bus,'trace');switched.bind_jobs(media.job_manager)
        result=await switched.inspect(active,req)
        assert result.result_id!=old.result_id and result.provider_id=='explicit-fake-vision'
        assert len(media.job_manager.all())==2
    asyncio.run(scenario())


def test_targeted_samples_are_bounded_and_use_source_time(monkeypatch):
    from movie_agent.providers import vision_sampling
    from movie_agent.media import TimeRange
    calls = []
    async def decode(args, **kwargs):
        calls.append(float(args[args.index('-ss') + 1]))
        return _png()
    monkeypatch.setattr(vision_sampling, '_run', decode)
    result = asyncio.run(vision_sampling.targeted_samples(b'bytes',
        [TimeRange(start_seconds=1, end_seconds=5), TimeRange(start_seconds=8, end_seconds=15)],
        last_frame_timestamp=14.958333))
    assert len(result) <= 8 and calls == sorted(set(calls))
    assert calls[0] == 1 and calls[-1] == 14.958333
    assert [time for time, _ in result] == calls


def test_runtime_escalates_when_configured_providers_cannot_execute_repair(tmp_path):
    async def scenario():
        artifacts, binaries = stores(tmp_path)
        class Diagnostic(MockVisionProvider):
            async def inspect(self, req):
                return VisionDecisionPolicy().commit(req, draft(issues=[issue()], proposed_decision='repair'),
                    provider_id=self.provider_id)
        registry = ProviderRegistry(); registry.register(Diagnostic())
        bus = LocalEventBus(); runtime = MediaRuntime(artifacts, binaries, registry, bus, 'trace')
        runtime.bind_jobs(JobManager(bus, 'trace'))
        req = request().model_copy(update={'target_sha256': None})
        job = GenerationJob(job_id=req.job_id, idempotency_key=req.job_id,
            project_id='project', task='vision', resource_class=ResourceClass.LIGHT)
        result = await runtime.inspect(job, req)
        assert result.proposed_decision == 'repair' and result.decision == 'human_review'
        assert result.decision_reasons == ['unsupported_or_unspecified_repair']
    asyncio.run(scenario())


def test_jpeg_video_transport_preserves_source_indices_times_and_hashes(tmp_path):
    async def scenario():
        artifacts, binaries = stores(tmp_path)
        req = pin_inspection(request().model_copy(update={'target_sha256': None}), artifacts, binaries)
        requested = []
        async def sample(content, indices):
            requested.extend(indices)
            return [_png() for _ in indices]
        async def transfer(content, digest, ext):
            return {'sha256': digest, 'size_bytes': len(content), 'deduplicated': True}
        provider = Qwen3VLVisionProvider(settings=MediaProviderSettings(),
            resolver=MediaReferenceBinaryResolver(artifacts, binaries), video_probe=fake_probe,
            video_sampler=sample, stager=SparkMediaStager(ResourceRuntimeSettings(), runner=transfer))
        body, metadata = await provider.prepare(req)
        url = body['messages'][1]['content'][1]['video_url']['url']
        assert url.startswith('data:video/jpeg;base64,')
        sequence = url.removeprefix('data:video/jpeg;base64,')
        assert len(sequence.split(',')) == 24
        assert requested[0] == 0 and requested[-1] == 359
        assert body['media_io_kwargs']['video'] == {
            'num_frames': 24, 'fps': 24, 'frames_indices': requested,
            'total_num_frames': 360, 'duration': 15, 'do_sample_frames': False}
        assert metadata['sampling']['proxy_sha256'] == sha256(sequence.encode()).hexdigest()
        assert metadata['sampling']['source_sha256'] == req.target_sha256
        assert metadata['sampling']['sample_count_is_requested_bound'] is False
    asyncio.run(scenario())


def test_human_directive_contract_retains_text():
    fields=dict(command_id='click-1',inspection_result_id='i',target_artifact_id='video',target_artifact_version=2,
        target_sha256='a'*64,disposition='custom_repair',feedback='  Keep the pause.\n加快跟拍。  ')
    directive=HumanRepairDirective(**fields,project_id='p',review_id='r',shot_id='s')
    assert directive.feedback==fields['feedback']
    with pytest.raises(ValueError): HumanRepairInput(**fields,accepted_issue_ids=['i'],dismissed_issue_ids=['i'])
    with pytest.raises(ValueError): HumanRepairInput(**{**fields,'feedback':' '})
