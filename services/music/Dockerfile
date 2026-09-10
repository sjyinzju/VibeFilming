FROM movie-agent-audio-runtime:p4d
ARG ACESTEP_REVISION=ca1e85fe9430179831e6bc6be790c332190a3866
WORKDIR /opt
RUN git clone --filter=blob:none --no-checkout https://github.com/ace-step/ACE-Step-1.5.git ace-step && cd ace-step && git checkout ${ACESTEP_REVISION}
WORKDIR /opt/ace-step
COPY requirements.txt /opt/music-requirements.txt
RUN pip install -r /opt/music-requirements.txt && pip install --no-deps -e .
COPY launch.py /opt/launch_music.py
RUN python -c "import acestep.api_server; import torch,torchaudio; print(torch.__version__,torchaudio.__version__)" && pip freeze > /opt/music-installed.txt
ENV ACESTEP_API_HOST=127.0.0.1 ACESTEP_API_PORT=8003 ACESTEP_API_WORKERS=1 ACESTEP_QUEUE_WORKERS=1 \
    ACESTEP_NO_INIT=false ACESTEP_CONFIG_PATH=acestep-v15-turbo ACESTEP_DEVICE=cuda \
    ACESTEP_INIT_LLM=false ACESTEP_USE_FLASH_ATTENTION=false ACESTEP_COMPILE_MODEL=false \
    ACESTEP_OFFLOAD_TO_CPU=false ACESTEP_TMPDIR=/output/tmp \
    ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-1.7B ACESTEP_LM_BACKEND=pt
CMD ["python", "/opt/launch_music.py"]
