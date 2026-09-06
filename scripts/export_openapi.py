"""Generate frontend contracts without a running server, provider, or credentials."""
import json
from pathlib import Path
from movie_agent.api.app import create_app
from movie_agent.domain import ProjectBrief

root = Path(__file__).resolve().parents[1] / "web" / "src" / "api"
root.mkdir(parents=True, exist_ok=True)
(root / "openapi.json").write_text(json.dumps(create_app(service=object()).openapi(), indent=2), encoding="utf-8")
(root / "brief.schema.json").write_text(json.dumps(ProjectBrief.model_json_schema(), indent=2), encoding="utf-8")
