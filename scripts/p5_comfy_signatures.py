from pathlib import Path
r=Path('/home/Developer/apps/ComfyUI')
for name,start,end in [('comfy/sd.py',1725,1795),('comfy_extras/nodes_edit_model.py',1,100),('comfy_extras/nodes_flux.py',1,58),('comfy/sd.py',1618,1675)]:
 p=r/name
 if p.exists():
  print(name)
  print('\n'.join(p.read_text().splitlines()[start-1:end]))
