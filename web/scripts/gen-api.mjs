import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
const python =
  process.env.MOVIE_AGENT_PYTHON ||
  (existsSync('../.venv312/Scripts/python.exe') ? '../.venv312/Scripts/python.exe' : 'python');
const result = spawnSync(python, ['../scripts/export_openapi.py'], { stdio: 'inherit' });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
