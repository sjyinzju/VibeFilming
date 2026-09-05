"""Real reasoning demo with mock downstream media; no serving infrastructure operations."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from movie_agent.config import LLMConfig
from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
from movie_agent.orchestration.runtime.contracts import RoleId
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.execution.durable_events import DurableLocalEventBus


async def run(args):
    provider = OpenAICompatibleLLMProvider(LLMConfig.from_env())
    try:
        if not await provider.health():
            print("Configured LLM endpoint/model unavailable; check the existing tunnel.")
            return 2
        roles = list(RoleId)
        if args.through_role:
            roles = roles[:roles.index(RoleId(args.through_role)) + 1]
        production = ReasoningMovieProduction(args.workspace, provider, real_roles=roles,
            event_bus=DurableLocalEventBus(Path(args.workspace) / "events"))
        if args.revise_role:
            if not args.resume:
                raise ValueError("--revise-role requires --resume")
            production.revise_failed_role(args.revise_role)
        if args.contract_schema_revision_scene:
            if not args.resume or args.revise_role or not args.authorization_reference:
                raise ValueError("Contract schema revision requires --resume and --authorization-reference, without --revise-role")
            production.authorize_contract_schema_revision(args.contract_schema_revision_scene, args.authorization_reference)
        if args.request_contract_type_revision_scene:
            if not args.resume or args.revise_role or args.contract_schema_revision_scene or not args.authorization_reference:
                raise ValueError("Request contract type revision requires --resume and --authorization-reference only")
            production.authorize_request_contract_type_revision(
                args.request_contract_type_revision_scene, args.authorization_reference)
        production.event_bus.subscribe(lambda e: print(json.dumps({"event": e.event_type.value,
            "node": e.node_id, "role": e.payload.get("role_id"), "scene_id": e.payload.get("scene_id"),
            "metrics": e.payload.get("metrics", e.payload if e.event_type.value.startswith("provider_request_") else None)}), flush=True)
            if e.event_type.value in {"node_started", "role_output_validated", "role_output_validation_failed",
                                     "provider_request_started", "provider_request_completed"} else None)
        result = await production.run(None if args.resume else production.load_brief(args.brief),
            resume=args.resume, stop_after_node=args.stop_after_node)
        print(json.dumps({"completed": result.completed, "project_id": result.project.project_id,
            "roles": list(production.role_results), "scenes": len(result.project.scenes),
            "shots": len(result.project.shots), "final_artifact_id": result.final_artifact_id,
            "checkpoint": result.latest_checkpoint_id}, indent=2))
        return 0
    finally:
        await provider.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("brief", nargs="?", default="tests/fixtures/sample_brief.json")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-after-node")
    parser.add_argument("--through-role", choices=[r.value for r in RoleId])
    parser.add_argument("--revise-role", choices=[r.value for r in RoleId])
    parser.add_argument("--contract-schema-revision-scene")
    parser.add_argument("--authorization-reference")
    parser.add_argument("--request-contract-type-revision-scene")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
