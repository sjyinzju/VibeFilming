"""Operator-only upload of explicitly listed, authorized P4D service files."""
from hashlib import sha256
import io
from pathlib import Path
import subprocess
import tarfile

from movie_agent.model_services.resources import ResourceRuntimeSettings

FILES = (
    'audio_runtime/Dockerfile', 'audio_runtime/requirements.txt',
    'tts/Dockerfile', 'tts/requirements.txt', 'tts/app.py',
    'music/Dockerfile', 'music/requirements.txt', 'music/launch.py',
)


def main():
    config = ResourceRuntimeSettings.from_env()
    # Fixed authorization scope. No repository archives, credentials or media.
    if (config.ssh_host, config.ssh_port, config.ssh_user) != ('106.13.186.155', 6081, 'Developer'):
        raise ValueError('This upload is authorized only for the specified Spark')
    root = Path(__file__).resolve().parents[1]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name in FILES:
            path = root / 'services' / name
            content = path.read_text(encoding='utf-8').replace('\r\n', '\n').encode()
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(content), 0o644
            archive.addfile(entry, io.BytesIO(content))
            print(name, len(content), sha256(content).hexdigest(), flush=True)
    payload = buffer.getvalue()
    command = ('set -eu; mkdir -p /home/Developer/services/audio-p4d/build; '
               'tar -xzf - -C /home/Developer/services/audio-p4d/build')
    subprocess.run(['ssh', '-p', str(config.ssh_port), '-i', config.ssh_key_path,
        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        f'{config.ssh_user}@{config.ssh_host}', command], input=payload, check=True)
    print(f'Uploaded only {len(FILES)} listed files; archive SHA256:', sha256(payload).hexdigest())


if __name__ == '__main__':
    main()
