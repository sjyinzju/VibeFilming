# Independent official CUDA wheel ABI; no NGC or ComfyUI site-packages inherited.
FROM ubuntu@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-dev build-essential git ca-certificates ffmpeg libsndfile1 sox && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/audio
ENV PATH="/opt/audio/bin:${PATH}" PYTHONUNBUFFERED=1 PIP_DEFAULT_TIMEOUT=180 PIP_RETRIES=5 \
    PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install pip==25.3 setuptools==71.1.0 wheel==0.45.1
COPY requirements.txt /opt/runtime-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r /opt/runtime-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu130
RUN python -c "import torch,torchaudio; assert torch.__version__.startswith('2.10.0'); assert torchaudio.__version__.startswith('2.10.0'); print(torch.__version__, torchaudio.__version__)"
RUN printf 'torch==2.10.0+cu130\ntorchaudio==2.10.0+cu130\ntorchvision==0.25.0+cu130\n' > /opt/abi-constraints.txt
ENV PIP_CONSTRAINT=/opt/abi-constraints.txt HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
