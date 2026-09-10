set -eu
uname -m
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv
free -b
docker ps --format '{{.Names}} {{.Status}}'
find /home/Developer/models/image/flux-kontext-dev-nvfp4 -maxdepth 3 -type f -printf '%P %s bytes\n' | head -80
docker inspect --format '{{.Config.Image}} {{json .Mounts}}' movie-agent-flux-direct
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | head -30
