import json,subprocess
d=json.loads(subprocess.check_output(['docker','inspect','movie-agent-vlm']))[0]
args=d['Config']['Cmd'] or []
allowed={'--model','--served-model-name','--reasoning-parser','--enable-auto-tool-choice',
    '--chat-template','--chat-template-kwargs','--max-model-len','--limit-mm-per-prompt','--mm-processor-kwargs'}
print({a:args[i+1] if i+1<len(args) and not args[i+1].startswith('--') else True
    for i,a in enumerate(args) if a in allowed})
print('reasoning_parser_flag_present',any('reasoning-parser' in a for a in args))
print('image',d['Config']['Image'])
print('positional_model',[a for a in args if a.startswith('/') and ('model' in a.lower() or 'qwen' in a.lower())])
print('mounts',[{'source':m['Source'],'destination':m['Destination'],'rw':m['RW']} for m in d['Mounts']])
code='from vllm.reasoning import ReasoningParserManager; print("qwen3_parser",ReasoningParserManager.get_reasoning_parser("qwen3").__name__); from vllm.v1.structured_output.backend_xgrammar import has_xgrammar_unsupported_json_features as u; print("bounded_array_unsupported",u({"type":"array","maxItems":12,"items":{"type":"string"}}))'
subprocess.run(['docker','run','--rm','--network','none','--entrypoint','python',d['Image'],'-c',code],check=True)
