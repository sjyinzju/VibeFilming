from types import SimpleNamespace as NS
import pytest
from movie_agent.artifacts import LocalArtifactStore
from movie_agent.domain import PromptPackage
from movie_agent.media import (MediaReference, VideoGenerationRequest, CameraMotionSpec, RepairContext,
    ProviderCapabilities, VideoCapabilities, MediaGenerationStrategy)
from movie_agent.media.video_conditioning import bind_reviewed_boundaries, reference_fingerprint
from movie_agent.comfyui import ComfyUIWorkflowRegistry, ComfyUIWorkflowCompiler
from movie_agent.comfyui.assets import ComfyUIAssetBridge
from movie_agent.providers.base import ProviderFailure


@pytest.mark.asyncio
@pytest.mark.parametrize('repair',[False,True])
async def test_complementary_references_compile_into_reviewed_h3_boundaries(tmp_path,repair):
    store=LocalArtifactStore(tmp_path)
    def pin(identity,kind,role=None):
        artifact=store.create_structured(identity,{'image':identity})
        return MediaReference(reference_type=kind,artifact_id=identity,version=artifact.version,
            sha256=artifact.metadata['sha256'],semantic_role=role)
    refs=[pin('face','character','portrait'),pin('body','character','wardrobe')]
    first,last=pin('start','first_frame'),pin('end','last_frame')
    store.create_structured('frame_gate_any_shot',{'passed':True,'reference_fingerprint':reference_fingerprint(refs),
        'frames':[first.model_dump(mode='json'),last.model_dump(mode='json')]})
    context=RepairContext(target_artifact_id='prior_video',target_artifact_version=1,target_sha256='a'*64,
        inspection_result_id='real_critique',fix=['Complete the reach'],preserve=['Preserve identity']) if repair else None
    request=VideoGenerationRequest(job_id='j',project_id='unrelated_project',shot_id='any_shot',output_artifact_id='video',
        prompt_package=PromptPackage(compiler_id='generic',compiler_version='1',positive_prompt='Reach for the cup.'),
        mode='first_last_frame_to_video',duration_seconds=1,fps=24,width=608,height=352,aspect_ratio='16:9',seed=42,
        camera_motion=CameraMotionSpec(motion_type='static'),first_frame=first,last_frame=last,
        references=[first,last,*refs],repair_context=context)
    profile,template,manifest=ComfyUIWorkflowRegistry().resolve('minimax_h3_fl2va')
    capabilities=ProviderCapabilities(provider_id='comfyui-video',kind='video',modalities=['video'],tasks=['video'],
        video=VideoCapabilities(first_frame=True,last_frame=True,first_last_frame=True,audio_generation=True))
    strategy=MediaGenerationStrategy(strategy_type='first_last_frame_to_video',reason='Reviewed boundaries',
        required_capabilities=['first_frame','last_frame','first_last_frame'])
    async def upload_image(**kwargs):return NS(name=kwargs['filename'],subfolder='movie-agent',type='input')
    bridge=ComfyUIAssetBridge(NS(upload_image=upload_image),NS(resolve=lambda r:NS(reference=r,content=b'image',mime_type='image/png')))
    async def compile(request):
        return ComfyUIWorkflowCompiler().compile(request=request,strategy=strategy,capabilities=capabilities,
            input_assets=await bridge.upload_video_inputs(request),template=template,binding_manifest=manifest,model_profile=profile.model_profile)
    with pytest.raises(ProviderFailure,match='does not bind input asset slot'):await compile(request)
    bound=bind_reviewed_boundaries(request,store)
    spec=await compile(bound)
    assert spec.prompt['105:104']['inputs']['first_frame']==['114',0]
    assert [r.semantic_role for r in bound.reference_conditioning.reference_assets]==['portrait','wardrobe']
    assert bound.repair_context==context
    changed=pin('body','character','wardrobe')
    stale=request.model_copy(update={'references':[first,last,refs[0],changed]})
    with pytest.raises(ValueError,match='Reference selection changed'):bind_reviewed_boundaries(stale,store)
