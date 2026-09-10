FROM movie-agent-audio-runtime:p4d
WORKDIR /opt/tts
COPY requirements.txt .
RUN pip install -r requirements.txt && pip install --no-deps qwen-tts==0.1.1
COPY app.py .
RUN python -c "from qwen_tts import Qwen3TTSModel; import torch,torchaudio; print(torch.__version__,torchaudio.__version__)" && pip freeze > /opt/tts/installed.txt
ENV TTS_MODEL_ROOT=/models/qwen3-tts TTS_CACHE_ROOT=/output
CMD ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8002"]
