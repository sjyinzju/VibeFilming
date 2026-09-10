"""Reversible, stopped-service-only clone: add the installed qwen3 reasoning parser."""
import copy,http.client,json,socket,subprocess

NAME='movie-agent-vlm';BACKUP='movie-agent-vlm-pre-p5-reasoning'
def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
old=inspect(NAME)
assert not old['State']['Running'],'VLM must already be stopped by P4A'
assert '--reasoning-parser' not in old['Config']['Cmd']
assert not subprocess.run(['docker','container','inspect',BACKUP],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0

class DockerConnection(http.client.HTTPConnection):
    def __init__(self):super().__init__('localhost')
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.connect('/var/run/docker.sock')
def api(method,path,payload=None):
    conn=DockerConnection();conn.request(method,path,body=json.dumps(payload) if payload is not None else None,
        headers={'Content-Type':'application/json'})
    response=conn.getresponse();data=response.read();conn.close()
    if response.status>=300:raise RuntimeError('Docker API operation failed: '+str(response.status))
    return json.loads(data) if data else None

version=api('GET','/version')['ApiVersion']
config=copy.deepcopy(old['Config'])
config['Image']=old['Image']  # existing image only; no pull or build
config['Cmd']=[*config['Cmd'],'--reasoning-parser','qwen3']
config['HostConfig']=copy.deepcopy(old['HostConfig'])
subprocess.run(['docker','rename',NAME,BACKUP],check=True)
try:
    created=api('POST','/v'+version+'/containers/create?name='+NAME,config)
except BaseException:
    subprocess.run(['docker','rename',BACKUP,NAME],check=True)
    raise
new=inspect(NAME)
assert not new['State']['Running'] and not inspect(BACKUP)['State']['Running']
assert new['Config']['Cmd']==config['Cmd'] and new['Image']==old['Image']
assert new['HostConfig']['Binds']==old['HostConfig']['Binds']
assert new['HostConfig']['DeviceRequests']==old['HostConfig']['DeviceRequests']
assert new['HostConfig']['PortBindings']==old['HostConfig']['PortBindings']
print(json.dumps({'service':NAME,'backup':BACKUP,'container_id':created['Id'],
    'change':['--reasoning-parser','qwen3'],'state':'created, stopped; P4A must start it',
    'same_image':True,'same_mounts':True,'same_gpu_requests':True,'same_ports':True,
    'model_downloads':0,'rollback':'With both stopped, rename the new service aside and rename the preserved backup to movie-agent-vlm.'}))
