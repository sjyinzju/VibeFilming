"""Explicit operator SSH helper; never imported by Domain or exposed to Studio."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('script', type=Path)
    parser.add_argument('--python', action='store_true')
    args = parser.parse_args()
    from movie_agent.model_services.resources import ResourceRuntimeSettings
    config = ResourceRuntimeSettings.from_env()
    result = subprocess.run(['ssh', '-p', str(config.ssh_port), '-i', config.ssh_key_path,
        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10',
        f'{config.ssh_user}@{config.ssh_host}', 'python3 -' if args.python else 'bash -se'],
        input=args.script.read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode())
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
