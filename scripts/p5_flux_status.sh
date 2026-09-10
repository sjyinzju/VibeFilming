set -eu
free -b
docker ps --format '{{.Names}} {{.Status}}'
docker inspect --format '{{json .State}}' movie-agent-flux-direct
docker logs --since 2026-09-09T15:45:00Z movie-agent-flux-direct 2>&1 | grep -v 'GET /health' | tail -45
