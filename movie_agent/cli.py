"""Command-line entry point for the complete local mock production."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from movie_agent.services import MockMovieProduction


def build_parser() -> argparse.ArgumentParser:
    """Build the deliberately small mock-runner argument parser."""

    parser = argparse.ArgumentParser(description="Run the model-free Movie Agent pipeline")
    parser.add_argument("brief", type=Path, help="Path to a ProjectBrief JSON file")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace/mock-production"),
        help="Durable artifact/checkpoint directory",
    )
    parser.add_argument("--resume", action="store_true", help="Resume the latest checkpoint")
    parser.add_argument(
        "--stop-after-node",
        help="Deliberately stop after a workflow node to demonstrate resume",
    )
    parser.add_argument(
        "--wait-for-human",
        action="store_true",
        help="Pause at the next human gate instead of using mock approval",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    production = MockMovieProduction(args.workspace)
    brief = None if args.resume else production.load_brief(args.brief)
    result = await production.run(
        brief,
        resume=args.resume,
        stop_after_node=args.stop_after_node,
        auto_approve=not args.wait_for_human,
    )
    print(
        json.dumps(
            {
                "completed": result.completed,
                "project_id": result.project.project_id,
                "final_artifact_id": result.final_artifact_id,
                "latest_checkpoint_id": result.latest_checkpoint_id,
                "jobs": len(result.jobs),
                "artifacts": len(result.artifacts),
                "evaluations": len(result.evaluations),
                "repair_plans": len(result.repair_plans),
                "workspace": str(args.workspace.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    """Run the async pipeline from a synchronous console script."""

    return asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
