set -eu
docker inspect --format '{{json .State}}' movie-agent-kontext
docker logs --tail 60 movie-agent-kontext
free -b
curl --max-time 5 -s http://127.0.0.1:9002/health || true
