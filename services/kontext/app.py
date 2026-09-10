"""Single-flight, localhost-only image edit API; binary inputs never name server paths."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hashlib import sha256
import base64
import io
import json
import threading

from PIL import Image
from backend import KontextBackend

backend = None
lock = threading.Lock()
CAPABILITIES = {'image_edit':True, 'image_to_image':False, 'text_to_image':False,
    'multi_reference':False, 'inpaint':False, 'outpaint':False, 'mask':False}


class Handler(BaseHTTPRequestHandler):
    def json(self, status, value):
        content=json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == '/health':
            return self.json(200, {'ready':backend is not None, 'busy':lock.locked(),
                'model':'FLUX.1-Kontext-dev-NVFP4', 'capabilities':CAPABILITIES,
                'environment':backend.environment if backend else None,
                'model_load_seconds':backend.load_seconds if backend else None})
        self.json(404, {'error':'not_found'})

    def do_POST(self):
        if self.path != '/v1/images/edit': return self.json(404, {'error':'not_found'})
        if not lock.acquire(blocking=False): return self.json(409, {'error':'busy'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 28*1024**2: raise ValueError('invalid body size')
            body=json.loads(self.rfile.read(length))
            allowed={'source_base64','source_sha256','prompt','width','height','steps','seed','guidance'}
            if set(body)-allowed: raise ValueError('unsupported parameters')
            source=base64.b64decode(body['source_base64'], validate=True)
            if sha256(source).hexdigest()!=body['source_sha256']: raise ValueError('source hash mismatch')
            width,height,steps,seed=body['width'],body['height'],body['steps'],body['seed']
            if any(type(v) is not int for v in (width,height,steps,seed)): raise ValueError('integer required')
            if not all(256<=d<=1024 and d%16==0 for d in (width,height)): raise ValueError('invalid dimensions')
            if not 1<=steps<=40 or not 0<=seed<2**63: raise ValueError('invalid steps/seed')
            guidance=body['guidance']
            if not isinstance(guidance,(float,int)) or not 0<=guidance<=10: raise ValueError('invalid guidance')
            prompt=body['prompt']
            if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>6000: raise ValueError('invalid instruction')
            with Image.open(io.BytesIO(source)) as decoded:
                if decoded.width*decoded.height>16_000_000: raise ValueError('source too large')
                source_image=decoded.convert('RGB')
            result,seconds=backend.edit(source_image,prompt,width,height,steps,seed,guidance)
            out=io.BytesIO(); result.save(out,format='PNG'); content=out.getvalue()
            self.send_response(200)
            for key,value in {'Content-Type':'image/png','Content-Length':str(len(content)),
                'X-Kontext-SHA256':sha256(content).hexdigest(), 'X-Kontext-Source-SHA256':body['source_sha256'],
                'X-Kontext-Seed':str(seed),'X-Kontext-Inference-Seconds':str(seconds),
                'X-Kontext-Load-Seconds':str(backend.load_seconds)}.items(): self.send_header(key,value)
            self.end_headers(); self.wfile.write(content)
        except (ValueError,KeyError,TypeError,OSError) as error:
            self.json(422, {'error':str(error)[:300]})
        except Exception as error:
            import traceback
            traceback.print_exc()
            self.json(500, {'error':type(error).__name__})
        finally: lock.release()


if __name__ == '__main__':
    backend=KontextBackend()
    print(json.dumps({'ready':True, **backend.environment, 'load_seconds':backend.load_seconds}),flush=True)
    ThreadingHTTPServer(('0.0.0.0',9002), Handler).serve_forever()
