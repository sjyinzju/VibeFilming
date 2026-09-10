set -eu
docker inspect --format '{{json .State}}' movie-agent-llm
docker logs --tail 18 movie-agent-llm
