"""Install service dependencies without replacing NVIDIA's preinstalled torch build."""

import os
from importlib import metadata
import subprocess
import sys
from pathlib import Path


def main():
    import torch

    original = torch.__version__
    # NVIDIA uses a different local-version suffix in wheel metadata than in torch.__version__.
    distribution_version = metadata.version("torch")
    requirements = Path(__file__).with_name("requirements.txt")
    wheels = Path(__file__).with_name("wheels")
    # Optional operator-supplied ARM64 wheel cache; never fall back online if incomplete.
    source_args = ["--no-index", "--find-links", str(wheels)] if wheels.is_dir() else []
    # Use an isolated --system-site-packages venv. Do not mutate the tested base image.
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Create/activate a venv with --system-site-packages first")
    subprocess.run([
        sys.executable, "-m", "pip", "install", *source_args,
        "-r", str(requirements), f"torch=={distribution_version}",
    ], check=True, env=os.environ.copy())
    # Fresh interpreter: do not let a cached import conceal an accidental replacement.
    subprocess.run([
        sys.executable, "-c",
        "import sys, torch, importlib.metadata as m; assert torch.__version__ == sys.argv[1] and m.version('torch') == sys.argv[2], 'NVIDIA torch changed'; print('Preserved NVIDIA torch:', torch.__version__, 'CUDA:', torch.version.cuda)",
        original, distribution_version,
    ], check=True)
    # Catch optional-package incompatibilities before the container starts loading weights.
    subprocess.run([sys.executable, "-c",
                    "from diffusers import FluxPipeline, FluxImg2ImgPipeline; print('FLUX pipeline imports verified')"],
                   check=True)


if __name__ == "__main__":
    main()
