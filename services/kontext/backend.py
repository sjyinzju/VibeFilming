"""Offline Kontext using the installed stock Comfy NVFP4 loader; no model downloads."""
import os
import sys
import time
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1')
sys.path.insert(0, '/workspace/ComfyUI')


class KontextBackend:
    def __init__(self):
        import torch
        import comfy.sd
        import comfy.utils
        started = time.perf_counter()
        base = Path('/home/Developer/models/image/flux-dev')
        weight = '/home/Developer/models/image/flux-kontext-dev-nvfp4/flux1-kontext-dev-nvfp4.safetensors'
        self.model = comfy.sd.load_diffusion_model(weight)
        clip_sd = comfy.utils.load_torch_file(str(base/'text_encoder/model.safetensors'))
        t5_sd = {}
        for shard in sorted((base/'text_encoder_2').glob('model-*-of-*.safetensors')):
            t5_sd.update(comfy.utils.load_torch_file(str(shard)))
        if not t5_sd:
            raise RuntimeError('missing local T5 shards')
        self.clip = comfy.sd.load_text_encoder_state_dicts([clip_sd, t5_sd],
            clip_type=comfy.sd.CLIPType.FLUX)
        self.vae = comfy.sd.VAE(sd=comfy.utils.load_torch_file(str(base/'ae.safetensors')))
        self.vae.throw_exception_if_invalid()
        self.load_seconds = time.perf_counter() - started
        self.environment = {'torch':torch.__version__, 'cuda':torch.version.cuda,
            'device':torch.cuda.get_device_name(), 'backend':'stock_comfy_nvfp4', 'offline':True}

    def edit(self, source, prompt, width, height, steps, seed, guidance):
        import numpy as np
        import torch
        import comfy.model_management
        import node_helpers
        import nodes
        from PIL import Image
        started = time.perf_counter()
        # Comfy retains quantized parameters across calls and mutates/offloads them
        # on the next call. Inference tensors cannot survive that lifecycle.
        with torch.no_grad():
            pixels = torch.from_numpy(np.asarray(source.convert('RGB').resize((width,height)),
                                                dtype=np.float32)/255.0)[None]
            reference = self.vae.encode(pixels)
            tokens = self.clip.tokenize(prompt)
            positive = self.clip.encode_from_tokens_scheduled(tokens, add_dict={'guidance':guidance})
            positive = node_helpers.conditioning_set_values(positive, {'reference_latents':[reference]}, append=True)
            latent = {'samples':torch.zeros([1,16,height//8,width//8],
                        device=comfy.model_management.intermediate_device())}
            output = nodes.common_ksampler(self.model, seed, steps, 1.0, 'euler', 'simple',
                                          positive, positive, latent)[0]
            decoded = self.vae.decode(output['samples'])[0].detach().cpu().numpy()
            image = Image.fromarray(np.clip(decoded*255,0,255).astype(np.uint8))
        return image, time.perf_counter()-started
