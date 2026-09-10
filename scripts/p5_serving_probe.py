import subprocess
code=r'''
from pathlib import Path
import vllm
root=Path(vllm.__file__).parent
for rel in ['v1/structured_output/backend_xgrammar.py','entrypoints/openai/chat_completion/serving.py']:
    p=root/rel
    print(str(p))
    lines=p.read_text().splitlines()
    for n,line in enumerate(lines):
        if any(token in line for token in ['validate_json_schema','create_error_response','except ','propertyNames']):
            print('\n'.join(f'{i+1}: {lines[i]}' for i in range(max(0,n-2),min(len(lines),n+9))))
'''
subprocess.run(['docker','exec','movie-agent-llm','python','-c',code],check=True)
