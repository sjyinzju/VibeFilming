"""Add only the new localhost Kontext forward; preserve existing tunnels."""
import subprocess
from movie_agent.model_services.resources import ResourceRuntimeSettings

if __name__=='__main__':
    s=ResourceRuntimeSettings.from_env()
    process=subprocess.Popen(['ssh','-N','-p',str(s.ssh_port),'-i',s.ssh_key_path,
        '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ExitOnForwardFailure=yes',
        '-o','ServerAliveInterval=30','-L','127.0.0.1:9002:127.0.0.1:9002',
        f'{s.ssh_user}@{s.ssh_host}'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    print('Kontext tunnel process',process.pid)
