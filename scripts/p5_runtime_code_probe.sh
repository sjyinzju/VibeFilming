set -eu
docker inspect --format '{{.Config.WorkingDir}} {{json .Config.Entrypoint}} {{json .Config.Cmd}} {{json .Mounts}}' movie-agent-comfyui
docker run --rm --network none --entrypoint python movie-agent-flux-direct:layer1 -c 'import torch,diffusers; print(torch.__version__,diffusers.__version__); from diffusers import FluxKontextPipeline; print(FluxKontextPipeline)'
docker run --rm --network none --entrypoint bash movie-agent-comfyui:26.08 -c 'pwd; find /opt -maxdepth 3 -type d -iname "*comfy*"; python -m pip show comfy-kitchen diffusers torch'
