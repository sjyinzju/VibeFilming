"""Launch the unmodified official API using only pre-existing checkpoints."""
import os
from pathlib import Path
import shutil


MODEL_NAMES = ('acestep-v15-turbo','vae','Qwen3-Embedding-0.6B','acestep-5Hz-lm-1.7B')


def local_checkpoint(name, directory):
    root=Path(directory).resolve()
    path=(root/name).resolve()
    if root not in path.parents or not (path/'config.json').is_file():
        raise RuntimeError('Required local checkpoint is missing; model downloads are disabled')
    return str(path)


def runtime_checkpoints(source, target):
    """Stage Python code; metadata and weights remain links into the RO mount.

    The official handler synchronizes its pinned Python implementation into a
    checkpoint directory. Give that operation a writable copy without ever
    copying, moving, or editing user weights or their original metadata.
    """
    for name in MODEL_NAMES:
        model = Path(local_checkpoint(name, source))
        if not any(model.glob('*.safetensors')):
            raise RuntimeError('Required local model weights are missing; downloads are disabled')
        for path in model.rglob('*'):
            relative = path.relative_to(source)
            if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
                continue
            destination = target / relative
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix == '.py':
                    if path.stat().st_size > 10 * 1024 * 1024 or destination.is_symlink():
                        raise RuntimeError('Unexpected runtime metadata layout')
                    shutil.copy2(path, destination)
                elif not destination.exists():
                    destination.symlink_to(path)
    return target


def main():
    root=Path('/opt/ace-step/checkpoints')
    overlay=runtime_checkpoints(root,Path('/output/runtime-checkpoints-v2'))
    os.environ['ACESTEP_CHECKPOINTS_DIR']=str(overlay)
    # Replace only the application download hook. Official model source/weights
    # remain unchanged; a missing checkpoint produces a visible error, never fetch.
    import acestep.api_server as server
    from acestep.model_downloader import check_main_model_exists, check_model_exists
    if not check_main_model_exists(overlay) or not check_model_exists('acestep-v15-turbo',overlay):
        raise RuntimeError('Local checkpoint preflight failed; downloads are disabled')
    server._ensure_model_downloaded=local_checkpoint
    import uvicorn
    uvicorn.run(server.app,host='127.0.0.1',port=8003,workers=1)


if __name__=='__main__':
    main()
