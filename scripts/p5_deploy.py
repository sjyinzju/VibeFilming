"""Explicit allow-list: two Kontext files; no credentials/workspaces/weights."""
import io
from pathlib import Path
import subprocess
import tarfile
from movie_agent.model_services.resources import ResourceRuntimeSettings

def main():
    settings=ResourceRuntimeSettings.from_env()
    if (settings.ssh_host,settings.ssh_port,settings.ssh_user)!=('106.13.186.155',6081,'Developer'):
        raise ValueError('unexpected deployment destination')
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        for name in ('app.py','backend.py'):
            content=(Path('services/kontext')/name).read_text(encoding='utf-8').replace('\r\n','\n').encode()
            info=tarfile.TarInfo(name); info.size=len(content); info.mode=0o644
            archive.addfile(info,io.BytesIO(content))
    subprocess.run(['ssh','-p',str(settings.ssh_port),'-i',settings.ssh_key_path,
        '-o','BatchMode=yes','-o','IdentitiesOnly=yes',f'{settings.ssh_user}@{settings.ssh_host}',
        'mkdir -p /home/Developer/services/kontext-p5; tar -xzf - -C /home/Developer/services/kontext-p5'],
        input=buffer.getvalue(),check=True)

if __name__=='__main__': main()
