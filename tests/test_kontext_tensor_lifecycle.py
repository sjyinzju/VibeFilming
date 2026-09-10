"""CPU tensor lifecycle regression; also runnable inside the installed Torch container."""
import sys
import types
import unittest
from unittest.mock import patch
try:
    import torch
except ImportError:
    torch=None


@unittest.skipIf(torch is None,'Requires installed Torch runtime')
class TensorLifecycleTest(unittest.TestCase):
    def test_retained_parameters_support_second_edit_and_offload(self):
        try:
            from services.kontext.backend import KontextBackend
        except ModuleNotFoundError:
            from backend import KontextBackend
        from PIL import Image
        retained=[]
        def encode(pixels):
            # Quantized module offload accesses the version counter of retained tensors.
            if retained:self.assertGreaterEqual(retained[-1]._version,0)
            retained.append(torch.ones(1))
            return pixels
        comfy=types.ModuleType('comfy');management=types.ModuleType('comfy.model_management')
        management.intermediate_device=lambda:'cpu';comfy.model_management=management
        modules={'comfy':comfy,'comfy.model_management':management,
            'node_helpers':types.SimpleNamespace(conditioning_set_values=lambda p,*args,**kwargs:p),
            'nodes':types.SimpleNamespace(common_ksampler=lambda *args:({'samples':torch.zeros(1,256,256,3)},))}
        backend=KontextBackend.__new__(KontextBackend);backend.model=object()
        backend.vae=types.SimpleNamespace(encode=encode,decode=lambda output:output)
        backend.clip=types.SimpleNamespace(tokenize=lambda p:p,encode_from_tokens_scheduled=lambda *args,**kwargs:[])
        with patch.dict(sys.modules,modules):
            for seed in (1,2):
                image,_=backend.edit(Image.new('RGB',(256,256)),'A person in a room',256,256,1,seed,2.5)
                self.assertEqual(image.size,(256,256))
        self.assertEqual(len(retained),2)
        self.assertFalse(any(t.is_inference() for t in retained))


if __name__=='__main__':unittest.main()
