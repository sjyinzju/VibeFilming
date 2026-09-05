"""Atomic local project metadata repository."""

import re
from pathlib import Path
from movie_agent.application.repository import ProjectRecord


class LocalProjectRepository:
    """Project metadata with validated path components and atomic replacement."""
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, project_id):
        if not re.fullmatch(r"project_[a-f0-9]{32}", project_id):
            raise KeyError(project_id)
        return self.root / project_id / "record.json"

    def save(self, record):
        path = self.path(record.project.project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        temp.replace(path)

    def get(self, project_id):
        path = self.path(project_id)
        if not path.exists():
            raise KeyError(project_id)
        return ProjectRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_ids(self):
        return sorted(p.parent.name for p in self.root.glob("project_*/record.json"))
