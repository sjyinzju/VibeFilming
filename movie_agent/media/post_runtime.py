"""Post commits and crash recovery through the existing MediaRuntime/JobManager."""
from hashlib import sha256
import json

from movie_agent.domain import Artifact, ArtifactType, Provenance, ProviderResult
from movie_agent.media.contracts import MediaModality
from movie_agent.media.post import PostRenderPlan, srt_bytes, stream_hash
from movie_agent.providers.media import normalize_media_provider_error


def commit_sidecar(runtime, identity, data, mime, extension, purpose, job, parents):
    digest = sha256(data).hexdigest()
    existing = next((a for a in runtime.artifact_store.list_versions(identity)
                     if a.metadata.get('sha256') == digest), None)
    if existing: return existing
    version = len(runtime.artifact_store.list_versions(identity))+1
    uri = f'artifact://{identity}/v{version}'
    # Only unregistered orphan bytes may be removed after an interrupted commit.
    if runtime.binary_store.exists(uri):
        runtime.binary_store.delete_if_unreferenced(uri,{a.uri for a in runtime.artifact_store.list_all()})
    binary = runtime.binary_store.put(identity,version,data,mime_type=mime,extension=extension)
    artifact = runtime.artifact_store.register(Artifact(artifact_id=identity,version=version,uri=binary.uri,
        artifact_type=ArtifactType.AUDIO if mime.startswith('audio/') else ArtifactType.SUBTITLE if purpose=='subtitles' else ArtifactType.TEXT,source_job_id=job.job_id,parent_artifact_ids=parents,
        metadata={'mock':False,'purpose':purpose,'mime_type':mime,'sha256':digest,'size_bytes':binary.size,
            **({'sample_rate':48000,'channels':2,'media':{'modality':'audio','purpose':purpose,
                'encoding':{'mime_type':mime,'format':'wav','codec':'pcm_f32le'},'mock':False}}
               if mime.startswith('audio/') else {})},
        provenance=Provenance(project_id=job.project_id,provider_id='ffmpeg-post',tool='deterministic_post',
                              input_artifact_ids=parents)))
    runtime._artifact_event(artifact,job)
    return artifact


def ensure_manifest(runtime, artifact, plan, plan_artifact, job):
    identity = f'{artifact.artifact_id}_manifest_v{artifact.version}'
    manifest = runtime.artifact_store.get(identity,1)
    if manifest: return manifest
    content = {'final_artifact':{'artifact_id':artifact.artifact_id,'version':artifact.version,
        'uri':artifact.uri,'sha256':artifact.metadata['sha256']},
        'render_plan':{**plan.model_dump(mode='json'),'artifact_version':plan_artifact.version,
                       'sha256':plan_artifact.metadata['sha256']},
        'render':artifact.provenance.parameters, 'qc':artifact.metadata['qc'],
        'subtitle_artifact':artifact.metadata.get('subtitle_artifact'),
        'audio_stems':artifact.metadata.get('audio_stems',[]), 'mock':False}
    return commit_sidecar(runtime,identity,json.dumps(content,ensure_ascii=False,indent=2).encode(),
        'application/json','json','render_manifest',job,[artifact.artifact_id,plan.render_plan_id])


async def run_real_post(runtime, provider, job, request):
    request = await provider.prepare_request(request)
    plan = PostRenderPlan.model_validate(request.render_plan)
    stable = f'post:{request.output_artifact_id}:{plan.render_plan_id}'
    if request.export_intent!='preview':stable+=':'+request.export_intent
    request = request.model_copy(update={'job_id':stable,'request_id':stable})
    job = job.model_copy(update={'job_id':stable,'idempotency_key':stable})
    parents = list(dict.fromkeys([plan.timeline['artifact_id'],plan.render_plan_id,
        *[s.video.artifact_id for s in plan.segments],*[s.audio.artifact_id for s in plan.segments if s.audio],
        *[l['source']['artifact_id'] for l in plan.audio_layers]]))
    plan_artifact = runtime.artifact_store.get(plan.render_plan_id,1)
    if not plan_artifact:
        plan_artifact = runtime.artifact_store.create_structured(plan.render_plan_id,plan.model_dump(mode='json'),
            metadata={'purpose':'post_render_plan','mock':False},source_job_id=stable,
            parent_artifact_ids=[p for p in parents if p != plan.render_plan_id],
            provenance=Provenance(project_id=job.project_id,provider_id=provider.provider_id,tool='timeline_conform'))
        runtime._artifact_event(plan_artifact,job)
    elif runtime.artifact_store.read_structured(plan_artifact) != plan.model_dump(mode='json'):
        raise ValueError('Committed RenderPlan mismatch')
    for existing in runtime.artifact_store.list_versions(request.output_artifact_id):
        if existing.metadata.get('render_plan_id') == plan.render_plan_id and existing.metadata.get('export_intent','preview')==request.export_intent:
            with runtime.binary_store.open(existing.uri) as stream:
                if stream_hash(stream) != existing.metadata['sha256']:
                    raise ValueError('Committed post output hash mismatch')
            ensure_manifest(runtime,existing,plan,plan_artifact,job)
            manager=runtime.job_manager
            if manager:
                from movie_agent.domain import JobStatus
                previous=next((j for j in manager.all() if j.job_id==stable),None)
                if not previous or previous.status!=JobStatus.SUCCEEDED:
                    if previous:manager._jobs[stable]=job
                    async def recovered(active):
                        return ProviderResult(provider_request_id=request.request_id,success=True,
                            artifact_ids=[existing.artifact_id],metadata={'recovered_committed_output':True})
                    await runtime._execute(job,provider.provider_id,request.request_id,recovered)
            return existing
    # A process crash may leave a running/failed job but no committed binary.
    # The immutable projection is unchanged; only this local deterministic job is retried.
    manager = runtime.job_manager
    if manager and stable in {j.job_id for j in manager.all()}:
        manager._jobs[stable] = job
    output = None
    async def operation(active):
        nonlocal output
        response = None
        try:
            async def progress(update):
                if runtime.job_manager.get(active.job_id).cancellation_requested:
                    await provider.cancel(request.request_id)
                runtime.job_manager.provider_activity(active.job_id,remote_status=update.status,
                    activity=update.activity,progress=update.progress,progress_is_determinate=update.progress_is_determinate,
                    remote_event=update.remote_event)
            response = await provider.process(request,on_progress=progress)
            if runtime.job_manager.get(active.job_id).cancellation_requested:
                from movie_agent.providers.base import ProviderFailure
                from movie_agent.domain import ProviderErrorType
                raise ProviderFailure('Post cancelled before commit',ProviderErrorType.CANCELLED)
            extra = dict(response.result.provider_metadata)
            extra['export_intent']=request.export_intent
            if request.export_intent == 'candidate':
                extra.update(candidate_status='technical_candidate_final', human_aesthetically_approved=False,
                    human_listening='pending', approval_policy='Technical candidate; aesthetic review remains pending')
            extra['render_plan_sha256'] = plan_artifact.metadata['sha256']
            extra['render_plan_version'] = plan_artifact.version
            extra['manifest_artifact_id'] = f'{request.output_artifact_id}_manifest_v{len(runtime.artifact_store.list_versions(request.output_artifact_id))+1}'
            extra['audio_stems'] = []
            for stem in response.payloads[1:]:
                # Use the same immutable binary/attachment infrastructure as the final film.
                content=stem.content.read()
                saved=commit_sidecar(runtime,stem.artifact_id,content,stem.mime_type,stem.extension,stem.purpose,active,parents)
                extra['audio_stems'].append({'artifact_id':saved.artifact_id,'version':saved.version,
                    'sha256':saved.metadata['sha256'],'name':stem.purpose})
            if plan.subtitles:
                subtitle = commit_sidecar(runtime,'subtitles_'+plan.render_plan_id[10:],srt_bytes(plan),
                    'application/x-subrip','srt','subtitles',active,[plan.render_plan_id])
                extra['subtitle_artifact'] = {'artifact_id':subtitle.artifact_id,'version':subtitle.version,
                                               'sha256':subtitle.metadata['sha256']}
                parents.append(subtitle.artifact_id)
            next_uri = f'artifact://{request.output_artifact_id}/v{len(runtime.artifact_store.list_versions(request.output_artifact_id))+1}'
            if runtime.binary_store.exists(next_uri):
                runtime.binary_store.delete_if_unreferenced(next_uri,{a.uri for a in runtime.artifact_store.list_all()})
            else:
                # A crash can occur between the immutable file rename and index commit.
                from movie_agent.media.storage import parse_artifact_uri
                identity,version=parse_artifact_uri(next_uri)
                orphan=(runtime.binary_store.root/identity/f'v{version}.mp4').resolve()
                if (runtime.binary_store.root in orphan.parents and orphan.is_file()
                        and not runtime.artifact_store.get(identity,version)):
                    orphan.unlink()
            response.result.provider_metadata.update(extra)
            output = runtime._register_response(job=active,response=response,modality=MediaModality.VIDEO,
                artifact_type=ArtifactType.FINAL_FILM if request.export_intent in {'approved_final','candidate'} else ArtifactType.VIDEO,
                purpose=request.output_artifact_id,input_ids=parents,prompt=None,seed=None,
                dimensions=response.result.dimensions,duration=response.result.duration_seconds,
                encoding=response.result.encoding,extra=extra)
            ensure_manifest(runtime,output,plan,plan_artifact,active)
            return ProviderResult(provider_request_id=request.request_id,success=True,artifact_ids=[output.artifact_id])
        except BaseException as error:
            return normalize_media_provider_error(request.request_id,error)
        finally:
            if response:
                for payload in response.payloads:
                    if hasattr(payload.content,'close'): payload.content.close()
    await runtime._execute(job,provider.provider_id,request.request_id,operation)
    if runtime.checkpoint_callback: runtime.checkpoint_callback()
    return output
