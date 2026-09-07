# Supply the exact NVIDIA PyTorch image already verified on this Spark node.
# No guessed latest tag, driver upgrade, model download, or bundled checkpoint.
ARG NVIDIA_PYTORCH_IMAGE
FROM ${NVIDIA_PYTORCH_IMAGE}

RUN python -m venv --system-site-packages /opt/flux-venv
ENV PATH="/opt/flux-venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    FLUX_PLACEMENT=cuda \
    FLUX_GENERATION_TIMEOUT_SECONDS=600
WORKDIR /opt/flux-direct
COPY . /opt/flux-direct/services/flux_image/
RUN python services/flux_image/install.py

# --network host is required at launch: the application binds only 127.0.0.1.
# Do not publish ports or override the host with 0.0.0.0.
CMD ["python", "-m", "services.flux_image"]
