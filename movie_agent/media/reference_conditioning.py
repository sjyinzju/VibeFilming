"""Explicit, deterministic single-image transport for semantically labeled references."""
from hashlib import sha256
from io import BytesIO
from math import ceil, sqrt
from PIL import Image, ImageDraw, ImageOps


def conditioning_transport(references, capabilities):
    for transport,flag in (('native','multi_reference'),('reference_sheet','reference_sheet')):
        for capability in capabilities:
            image=capability.image
            if image and getattr(image,flag) and (image.max_reference_images is None or len(references)<=image.max_reference_images):
                return transport
    return None


def compose_reference_sheet(references, resolver, *, width=1024, height=1024, maximum=6):
    if not 1<=len(references)<=maximum:
        raise ValueError('Reference sheet exceeds the declared conditioning bound')
    columns=ceil(sqrt(len(references)));rows=ceil(len(references)/columns)
    cell_width,cell_height=width//columns,height//rows
    canvas=Image.new('RGB',(width,height),'#e1e3e4');draw=ImageDraw.Draw(canvas)
    bindings=[]
    for index,ref in enumerate(references):
        if ref.version is None or ref.sha256 is None:raise ValueError('Conditioning references require exact pins')
        resolved=resolver.resolve(ref)
        if sha256(resolved.content).hexdigest()!=ref.sha256:raise ValueError('Conditioning source hash mismatch')
        x=(index%columns)*cell_width;y=(index//columns)*cell_height
        with Image.open(BytesIO(resolved.content)) as image:
            image=ImageOps.contain(image.convert('RGB'),(cell_width-16,cell_height-42))
            canvas.paste(image,(x+(cell_width-image.width)//2,y+32+(cell_height-42-image.height)//2))
        role=ref.semantic_role or ref.reference_type.value
        draw.text((x+8,y+8),f'{index+1}: {role}',fill='black')
        bindings.append({'panel':index+1,'entity_id':ref.entity_id,'semantic_role':role,
            'artifact_id':ref.artifact_id,'version':ref.version,'sha256':ref.sha256,
            'human_acceptance_artifact_id':ref.human_acceptance_artifact_id,
            'accepted_limitations':ref.accepted_limitations,'rectangle':[x,y,cell_width,cell_height]})
    output=BytesIO();canvas.save(output,format='PNG');content=output.getvalue()
    return content,{'transport':'reference_sheet','native_multi_reference':False,
        'sheet_sha256':sha256(content).hexdigest(),'layout_revision':'semantic-panels-1','bindings':bindings}
