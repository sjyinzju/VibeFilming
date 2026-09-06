import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
const root = resolve('..');
const python =
  process.env.MOVIE_AGENT_PYTHON ||
  (existsSync('../.venv312/Scripts/python.exe')
    ? resolve('../.venv312/Scripts/python.exe')
    : 'python');
const child = spawn(
  python,
  [
    '-m',
    'uvicorn',
    'tests.p2b_server:create_app',
    '--factory',
    '--host',
    '127.0.0.1',
    '--port',
    '8081',
  ],
  { cwd: root, stdio: 'inherit' },
);
child.on('error', (error) => {
  console.error(error.message);
  process.exit(1);
});
child.on('exit', (code) => process.exit(code ?? 0));
process.on('SIGTERM', () => child.kill('SIGTERM'));
process.on('SIGINT', () => child.kill('SIGINT'));
