set -eu
docker logs --tail 90 movie-agent-vlm
docker inspect --format '{{json .State}}' movie-agent-vlm
