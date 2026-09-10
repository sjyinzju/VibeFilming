"""Exact immutable attachment serving, separate from inline byte-range preview."""
from dataclasses import dataclass
from pathlib import Path
import re

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from movie_agent.media.post import stream_hash
from movie_agent.media.storage import parse_artifact_uri


@dataclass(frozen=True)
class DownloadRecord:
    path: Path
    mime: str
    extension: str
    size: int
    sha256: str


class ArtifactBinaryResolver:
    def __init__(self, artifacts, binaries):
        self.artifacts, self.binaries = artifacts, binaries

    def resolve(self, artifact, project_id):
        if not artifact or artifact.provenance.project_id != project_id:
            raise HTTPException(404,'Artifact not found in this project')
        identity,version = parse_artifact_uri(artifact.uri)
        if (identity,version)!=(artifact.artifact_id,artifact.version):
            raise HTTPException(404,'Artifact identity mismatch')
        if self.binaries.exists(artifact.uri):
            record = self.binaries.describe(artifact.uri)
            path,mime,extension = record.path,record.mime_type,record.extension
        elif artifact.metadata.get('mime_type')=='application/json':
            path = (self.artifacts.data_dir / identity / f'v{version}.json').resolve()
            if self.artifacts.data_dir.resolve() not in path.parents or not path.is_file():
                raise HTTPException(404,'Artifact content unavailable')
            mime,extension = 'application/json','json'
        else:
            raise HTTPException(404,'Artifact content unavailable')
        with path.open('rb') as stream: digest=stream_hash(stream)
        if artifact.metadata.get('sha256') and digest!=artifact.metadata['sha256']:
            raise HTTPException(409,'Artifact integrity check failed')
        return DownloadRecord(path,mime,extension,path.stat().st_size,digest)


def download_response(artifact, record):
    filename = re.sub(r'[^A-Za-z0-9_.-]','_',artifact.artifact_id)[:180]
    filename = f'{filename}_v{artifact.version}.{record.extension}'
    def body():
        with record.path.open('rb') as stream:
            while chunk:=stream.read(1024*1024): yield chunk
    return StreamingResponse(body(),media_type=record.mime,headers={
        'Content-Length':str(record.size),'Content-Disposition':f'attachment; filename="{filename}"',
        'ETag':f'"{record.sha256}"','X-Artifact-Id':artifact.artifact_id,
        'X-Artifact-Version':str(artifact.version),'X-Artifact-SHA256':record.sha256,
        'X-Content-Type-Options':'nosniff','Cache-Control':'private, max-age=31536000, immutable'})


def public_artifact(artifact):
    """Legacy placeholders used file URIs; the browser receives only opaque identity."""
    def clean(value):
        if isinstance(value,dict): return {k:clean(v) for k,v in value.items()}
        if isinstance(value,list): return [clean(v) for v in value]
        if isinstance(value,str) and (value.startswith('file:') or re.search(r'\b[A-Za-z]:[\\/]',value)):
            return '[internal path]'
        return value
    from movie_agent.domain import Artifact
    payload = clean(artifact.model_dump(mode='json'))
    payload['uri']=f'artifact://{artifact.artifact_id}/v{artifact.version}'
    return Artifact.model_validate(payload)
