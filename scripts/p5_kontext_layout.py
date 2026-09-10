import json
import struct
from pathlib import Path
root = Path('/home/Developer/models/image/flux-kontext-dev-nvfp4')
print((root/'README.md').read_text())
print((root/'configuration.json').read_text())
with (root/'flux1-kontext-dev-nvfp4.safetensors').open('rb') as f:
    size = struct.unpack('<Q', f.read(8))[0]
    header = json.loads(f.read(size))
print('metadata', {k:v for k,v in header.get('__metadata__', {}).items() if len(v) < 500})
print('tensor_count', len(header)-1)
print(json.dumps({k:v for k,v in list(header.items())[:35] if k != '__metadata__'}, indent=2))
for p in Path('/home/Developer/models/image/flux-dev').rglob('*'):
    if p.is_file(): print(str(p.relative_to('/home/Developer/models/image/flux-dev')), p.stat().st_size)
