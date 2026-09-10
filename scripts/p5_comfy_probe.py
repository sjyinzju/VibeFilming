from pathlib import Path
root=Path('/home/Developer/apps/ComfyUI')
for name, terms in {
 'comfy_extras/nodes_flux.py':['class FluxKontext','class ReferenceLatent','class FluxGuidance'],
 'comfy/sd.py':['def load_diffusion_model(', 'def load_clip(', 'class VAE'],
 'comfy/ops.py':['nvfp4','quantization'],
 'nodes.py':['class CLIPTextEncode', 'class VAEDecode', 'class VAEEncode', 'def common_ksampler'],
 'extra_model_paths.yaml':['flux'],
}.items():
 p=root/name
 if not p.exists(): continue
 lines=p.read_text().splitlines()
 print('\nFILE',name)
 indices=[i for i,l in enumerate(lines) if any(t in l for t in terms)]
 for i in indices[:8]: print('\n'.join(f'{j+1}: {lines[j]}' for j in range(max(0,i-2), min(i+35,len(lines)))))
print('text encoder files')
for p in Path('/home/Developer/models').rglob('*'):
 if p.is_file() and any(s in p.name.lower() for s in ['t5xxl','clip_l','ae.safetensors']): print(p,p.stat().st_size)
