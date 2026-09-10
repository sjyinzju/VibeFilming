set -eu
for name in movie-agent-llm movie-agent-flux-direct movie-agent-comfyui movie-agent-vlm movie-agent-tts movie-agent-music; do
  test "$(docker inspect --format '{{.State.Running}}' "$name")" = false || { echo "Isolated smoke blocked: $name running"; exit 1; }
done
free -b
if docker inspect movie-agent-kontext >/dev/null 2>&1; then
  echo 'Existing Kontext container retained; use P4A lifecycle after inspection'
  exit 1
fi
docker create --name movie-agent-kontext --gpus all --ipc host --network bridge \
  -p 127.0.0.1:9002:9002 --entrypoint python \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e PYTHONDONTWRITEBYTECODE=1 \
  -v /home/Developer/apps/ComfyUI:/workspace/ComfyUI:ro \
  -v /home/Developer/models/image/flux-dev:/home/Developer/models/image/flux-dev:ro \
  -v /home/Developer/models/image/flux-kontext-dev-nvfp4:/home/Developer/models/image/flux-kontext-dev-nvfp4:ro \
  -v /home/Developer/services/kontext-p5:/service:ro \
  movie-agent-comfyui:26.08 /service/app.py
docker start movie-agent-kontext
docker logs --tail 25 movie-agent-kontext
