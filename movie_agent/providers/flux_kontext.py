"""Single-source instruction edit through existing ImageProvider and Artifact runtime."""
import base64
from hashlib import sha256
import io
import math

import httpx
from PIL import Image

from movie_agent.domain import Provenance, ProviderErrorType, QualityProfile
from movie_agent.media import ImageCapabilities, ImageGenerationMode, ImageGenerationResult, MediaDimensions, MediaEncoding
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.flux_direct import FluxDirectImageProvider
from movie_agent.providers.media import BinaryPayload, ProviderMediaResponse


class FluxKontextImageProvider(FluxDirectImageProvider):
    provider_id = 'flux_kontext'
    model_service_id = 'kontext'

    async def health(self):
        try:
            async with self._client() as client:
                r=await client.get('health',timeout=5)
                data=r.json()
            return r.status_code==200 and data.get('ready') is True and data.get('model')=='FLUX.1-Kontext-dev-NVFP4'
        except (httpx.HTTPError,ValueError): return False

    async def capabilities(self):
        caps=await super().capabilities()
        return caps.model_copy(update={'provider_id':self.provider_id, 'quality_profiles':list(QualityProfile),
            'image':ImageCapabilities(image_edit=True,max_width=1024,max_height=1024,
                                     min_dimension=256,dimension_multiple=16,reference_sheet=True,max_reference_images=6)})

    async def generate(self, request):
        source=request.source_image
        sheet=request.reference_conditioning=='reference_sheet'
        if (request.mode!=ImageGenerationMode.IMAGE_EDIT or request.mask_artifact_id or self.resolver is None
            or (sheet and (source is not None or not request.references))
            or (not sheet and (source is None or request.references or source.version is None or source.sha256 is None))):
            raise ProviderFailure('Kontext requires one exact version/hash source and image_edit only',ProviderErrorType.INVALID_REQUEST)
        if any(not 256<=d<=1024 or d%16 for d in (request.width,request.height)):
            raise ProviderFailure('Kontext dimensions outside measured contract',ProviderErrorType.INVALID_REQUEST)
        options=dict(request.provider_parameters)
        if set(options)-{'steps','guidance','preserve','change'}:
            raise ProviderFailure('Kontext does not support requested option',ProviderErrorType.UNSUPPORTED_CAPABILITY)
        conditioning={}
        if sheet:
            from movie_agent.media.reference_conditioning import compose_reference_sheet
            source_content,conditioning=compose_reference_sheet(request.references,self.resolver)
            source_digest=conditioning['sheet_sha256']
        else:
            source_content=self.resolver.resolve(source).content
            source_digest=source.sha256
        if sha256(source_content).hexdigest()!=source_digest:
            raise ProviderFailure('Kontext source hash mismatch',ProviderErrorType.MEDIA_CORRUPT)
        preserve=options.pop('preserve',[]); change=options.pop('change',[])
        if any(not isinstance(v,list) or any(not isinstance(s,str) for s in v) for v in (preserve,change)):
            raise ProviderFailure('Preserve/change requirements must be strings',ProviderErrorType.INVALID_REQUEST)
        prompt=request.prompt_package.positive_prompt
        if sheet:
            import json
            labels=[{k:b[k] for k in ('panel','entity_id','semantic_role')} for b in conditioning['bindings']]
            prompt=('Create ONE coherent cinematic scene from the reference sheet. '+prompt+
                '\nEach numbered panel defines only its entity and semantic role: '+json.dumps(labels,ensure_ascii=True)+
                '. Combine the face and wardrobe panels for each entity. Use environment panels for setting. '
                'Do not copy reference poses, labels, borders or the sheet layout.')
        if preserve: prompt+='\nPreserve: '+'; '.join(preserve)
        if change: prompt+='\nChange: '+'; '.join(change)
        body={'source_base64':base64.b64encode(source_content).decode('ascii'), 'source_sha256':source_digest,
            'prompt':prompt,'width':request.width,'height':request.height,'steps':options.get('steps',28),
            'seed':request.seed if request.seed is not None else 42,'guidance':options.get('guidance',2.5)}
        try:
            async with self._client() as client:
                response=await client.post('v1/images/edit',json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            # A received rejection is a definite response, not a lost request.
            status=error.response.status_code
            kind=(ProviderErrorType.INVALID_REQUEST if status==422 else
                  ProviderErrorType.RESOURCE_EXHAUSTED if status==409 else ProviderErrorType.GENERATION_FAILED)
            raise ProviderFailure(f'Kontext returned HTTP {status}',kind) from error
        except httpx.HTTPError as error:
            raise ProviderFailure('Kontext edit transport failed; do not replay uncertain inference',ProviderErrorType.UNAVAILABLE) from error
        try:
            content=response.content
            digest=sha256(content).hexdigest()
            if len(content)>16*1024**2 or response.headers.get('content-type')!='image/png': raise ValueError('invalid PNG')
            if response.headers['x-kontext-sha256']!=digest or response.headers['x-kontext-source-sha256']!=source_digest:
                raise ValueError('hash mismatch')
            if int(response.headers['x-kontext-seed'])!=body['seed']: raise ValueError('seed mismatch')
            seconds=float(response.headers['x-kontext-inference-seconds'])
            if not math.isfinite(seconds) or seconds<0: raise ValueError('invalid timing')
            with Image.open(io.BytesIO(content)) as decoded:
                if decoded.size!=(request.width,request.height): raise ValueError('dimension mismatch')
                decoded.verify()
        except (KeyError,ValueError,OSError) as error:
            raise ProviderFailure('Kontext response failed integrity checks',ProviderErrorType.MEDIA_CORRUPT) from error
        from movie_agent.quality.budget import fingerprint
        parameters={'mock':False,'model':'FLUX.1-Kontext-dev-NVFP4', 'backend':'stock_comfy_nvfp4',
            'source_artifact_id':source.artifact_id if source else None,'source_version':source.version if source else None,'source_sha256':source_digest,
            'instruction':prompt,'preserve':preserve,'change':change,'sha256':digest,'inference_seconds':seconds,
            'reference_conditioning':conditioning or None,
            'input_fingerprint':fingerprint(conditioning['bindings'] if sheet else source.model_dump(mode='json',exclude={'reference_id'})),
            'config_fingerprint':fingerprint({k:v for k,v in body.items() if k!='source_base64'})}
        result=ImageGenerationResult(request_id=request.request_id,artifact_ids=[request.output_artifact_id],
            primary_artifact_id=request.output_artifact_id,provider_id=self.provider_id,model_service_id=self.model_service_id,
            seed=body['seed'],dimensions=MediaDimensions(width=request.width,height=request.height,aspect_ratio=request.aspect_ratio),
            encoding=MediaEncoding(mime_type='image/png',format='png',codec='png'),provider_metadata=parameters,
            provenance=Provenance(provider_id=self.provider_id,model_service_id=self.model_service_id,
                                  input_artifact_ids=request.input_artifact_ids,parameters=parameters))
        self._results[request.request_id]=result
        return ProviderMediaResponse(result,(BinaryPayload(request.output_artifact_id,content,'image/png','png',request.purpose.value),))
