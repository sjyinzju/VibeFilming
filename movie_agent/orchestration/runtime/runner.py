"""Uniform role calls: context -> provider -> schema -> semantics -> candidate result."""

import json
import math
from copy import deepcopy
from movie_agent.domain import (
    EventEnvelope, EventType, GenerationRequest, GenerationStrategy, GenerationStrategyType,
    PromptPackage, Project, ProviderRequest, ResourceClass, Scene, utc_now,
)
from movie_agent.execution.events import EventBus
from movie_agent.providers.base import LLMProvider, ProviderFailure
from movie_agent.providers.openai_compatible import CompletionOptions
from .contracts import RoleInvocation, RoleResult, RoleAttempt, InferenceMetrics, ValidationReplay
from .budgets import planning_output_budget
from .context import ContextBuilder, content_hash
from .registry import RoleRegistry
from .language import LanguagePolicy
from .validation import RoleOutputValidator
from .cinematographer_drafts import MAPPER_VERSION, ShotPlanDraft
from .terminal import (TerminalRepairDraft, terminal_delta, terminal_repair_prompt,
                       merge_terminal_repair, is_terminal_only)
from .contracts import ValidationReport, OutputIssue, OutputErrorCode


class StructuredOutputAdapter:
    """Export the actual target contract schema into the serving request."""

    def response_format(self, target, *, max_scene_shots: int | None = None, scene: Scene | None = None):
        schema = target.model_json_schema()
        # Strict serving schemas must require defaulted fields too. Otherwise an
        # LLM can repeatedly omit scene membership/continuity fields that Pydantic
        # fills with empty defaults. This changes decoding, not persisted contracts.
        def require_properties(value):
            if isinstance(value, dict):
                if "properties" in value:
                    value["required"] = list(value["properties"])
                for child in value.values():
                    require_properties(child)
            elif isinstance(value, list):
                for child in value:
                    require_properties(child)
        require_properties(schema)
        if max_scene_shots is not None:
            if target.__name__ not in {"ShotPlan", "ShotPlanDraft"} or max_scene_shots < 1:
                raise ValueError("Scene decoding bounds require a positive ShotPlan budget")
            schema["properties"]["shots"]["maxItems"] = max_scene_shots
            if target.__name__ == "ShotPlan":
                schema["properties"]["continuity_chains"]["maxItems"] = max_scene_shots
                schema["$defs"]["ContinuityChain"]["properties"]["shot_ids"].update(
                    minItems=1, maxItems=max_scene_shots)
        if scene is not None and target.__name__ == "ShotPlanDraft":
            local = schema["$defs"]["ShotLocalStateDraft"]["properties"]
            bindings = (("character_updates", "CharacterLocalStateDraft", "character_id", scene.character_ids),
                        ("location_updates", "LocationLocalStateDraft", "location_id", [scene.location_id]),
                        ("prop_updates", "PropLocalStateDraft", "prop_id", scene.prop_ids))
            for collection, definition, identity, allowed in bindings:
                local[collection]["maxItems"] = len(set(allowed))
                if allowed:
                    schema["$defs"][definition]["properties"][identity]["enum"] = sorted(set(allowed))
        elif scene is not None:
            if target.__name__ != "ShotPlan":
                raise ValueError("Scene boundary constraints require ShotPlan")
            # Two existing semantic invariants need distinct decoding schemas:
            # Shot maps are scene-scoped; chain state preserves ambient canon too.
            boundary = deepcopy(schema["$defs"]["ContinuityState"])
            for field, allowed in (("character_states", scene.character_ids),
                                   ("prop_states", scene.prop_ids), ("location_states", [scene.location_id])):
                mapping = boundary["properties"][field]
                value_schema = mapping["additionalProperties"]
                mapping["properties"] = {key: deepcopy(value_schema) for key in sorted(set(allowed))}
                mapping["additionalProperties"] = False
            schema["$defs"]["ShotBoundaryState"] = boundary
            for field in ("state_before", "expected_state_after"):
                schema["$defs"]["Shot"]["properties"][field]["$ref"] = "#/$defs/ShotBoundaryState"
            schema["$defs"]["ContinuityChain"]["properties"]["initial_state"]["const"] = (
                scene.initial_state.model_dump(mode="json"))
        return {"type": "json_schema", "json_schema": {
            "name": target.__name__, "strict": True, "schema": schema}}


class LLMOutputRepairer:
    """Build a correction request without mutating, dropping, or guessing fields."""

    def prompt(self, original: str, raw: str, report, target_schema: dict) -> str:
        return json.dumps({"original_task": original, "previous_output": raw,
            "target_contract": target_schema,
            "validation_errors": report.model_dump(mode="json"),
            "instruction": "Correct all listed errors. Return the SAME target schema supplied in response_format."},
            ensure_ascii=False)


class RoleOutputInvalid(RuntimeError):
    """Finite validation budget exhausted; carries the audit record for checkpointing."""

    def __init__(self, result: RoleResult):
        super().__init__("ROLE_OUTPUT_INVALID: " + result.invocation.role_id.value)
        self.result = result


class RoleRunner:
    """One execution path for every role. Returns validated candidates, never edits Project."""

    def __init__(self, provider: LLMProvider, event_bus: EventBus, trace_id: str,
                 registry: RoleRegistry | None = None) -> None:
        self.provider, self.event_bus, self.trace_id = provider, event_bus, trace_id
        self.registry = registry or RoleRegistry()
        self.context_builder = ContextBuilder()
        self.adapter = StructuredOutputAdapter()
        self.validator = RoleOutputValidator()
        self.repairer = LLMOutputRepairer()

    async def run(self, invocation: RoleInvocation, project: Project, *, scene: Scene | None = None,
                  previous: RoleResult | None = None, on_attempt=None) -> RoleResult:
        definition = self.registry.get(invocation.role_id)
        system_instruction = definition.instruction + '\n\n' + LanguagePolicy.instruction(
            project.brief.output_language)
        target = self.registry.target(definition)
        context = self.context_builder.build(definition, project, scene=scene)
        result = previous or RoleResult(invocation=invocation, provider_id=self.provider.provider_id,
            context=context, target_schema=target.__name__)
        if previous and previous.context.context_hash != context.context_hash:
            raise ValueError("Role context changed since saved attempt; explicit revision is required")
        if result.output is not None and result.committed:
            return result
        original = json.dumps(context.payload, ensure_ascii=False, sort_keys=True)
        prompt = original
        response_format = self.adapter.response_format(target,
            max_scene_shots=context.payload["scene_shot_budget"] if scene else None, scene=scene)
        revision = invocation.contract_revision
        if result.failure_code == "CONTRACT_STATE_SCOPE_CONFLICT":
            raise RoleOutputInvalid(result)
        if revision and content_hash(response_format) != revision.request_schema_hash:
            raise ValueError("Authorized request schema changed; no implicit revision allowed")
        type_revision = invocation.request_contract_revision
        if type_revision and content_hash(response_format) != type_revision.new_schema_hash:
            raise ValueError("Authorized request contract type changed; no implicit revision allowed")
        explicit_budget = revision.repair_budget if revision else (
            type_revision.repair_budget if type_revision else definition.output_policy.repair_budget)
        repair_budget = min(definition.output_policy.repair_budget, explicit_budget)
        if invocation.semantic_revision:
            repair_budget = min(repair_budget, invocation.semantic_revision.repair_budget)
            if content_hash(scene.expected_final_state.model_dump(mode="json")) != invocation.semantic_revision.terminal_target_hash:
                raise ValueError("Authorized terminal target changed")
        # An exhausted real output may be replayed through deterministic
        # validation after a mapper/validator code fix. This consumes no
        # provider request and preserves every original attempt and metric.
        if (previous is not None and result.pending_output is not None
                and len(result.attempts) > repair_budget):
            source_request_id = result.attempts[-1].request_id
            source_output_hash = content_hash(result.pending_output)
            output, replay_report = self.validator.parse(
                target, result.pending_output, project, scene=scene)
            replay = ValidationReplay(source_request_id=source_request_id,
                source_output_hash=source_output_hash,
                mapper_version=MAPPER_VERSION if target is ShotPlanDraft else None,
                validation=replay_report)
            if not any(item.source_request_id == replay.source_request_id
                       and item.source_output_hash == replay.source_output_hash
                       and item.mapper_version == replay.mapper_version
                       for item in result.validation_replays):
                result.validation_replays.append(replay)
            if replay_report.valid:
                result.output = output.model_dump(mode="json")
                result.failure_code = None
                result.pending_output = None
                self._emit(EventType.ROLE_OUTPUT_VALIDATED, invocation, {
                    "target_schema": target.__name__, "validation_replay": True,
                    "source_request_id": source_request_id,
                    "source_output_hash": source_output_hash,
                    "mapper_version": replay.mapper_version,
                    "context_hash": context.context_hash})
                if on_attempt:
                    on_attempt(result)
                return result
            if on_attempt:
                on_attempt(result)
        if scene:
            request_context = {**context.payload, "required_terminal_delta":
                terminal_delta(scene, scene.initial_state).model_dump(mode="json")}
            original = json.dumps(request_context, ensure_ascii=False, sort_keys=True)
            prompt = original
        policy = definition.inference_policy.resolve(self.provider.inference_defaults(),
            max_tokens=definition.output_policy.max_tokens)
        output_ceiling = planning_output_budget(invocation.role_id, project, scene=scene)
        if output_ceiling is not None:
            policy.max_output_tokens = min(policy.max_output_tokens, output_ceiling)
            system_instruction += (
                '\nReturn compact JSON without indentation. Keep natural-language descriptions concise; '
                'avoid repeating background prose. Include ALL required fields and verbatim commitments. '
                'ScenePlan must include all existing screenplay scenes; ShotPlanDraft covers ONLY the '
                'current scene. Never truncate or omit required content to fit. '
                f'The output ceiling is {policy.max_output_tokens} tokens.')
        if result.attempts and result.pending_output is not None:
            prompt = self.repairer.prompt(original, result.pending_output, result.attempts[-1].validation,
                                          response_format["json_schema"])
        for index in range(len(result.attempts), repair_budget + 1):
            request_format = response_format
            request_instruction = system_instruction
            # Only terminal conflicts use the small patch contract. Full-plan
            # validation still runs after deterministic merge, before commit.
            base = result.terminal_repair_base
            report = result.attempts[-1].validation if result.attempts else ValidationReport()
            if scene and target is ShotPlanDraft and not base and result.pending_output and is_terminal_only(report):
                base = result.pending_output
                result.terminal_repair_base = base
            if base:
                if (invocation.semantic_revision and not result.attempts
                        and content_hash(base) != invocation.semantic_revision.source_output_hash):
                    raise ValueError('Authorized semantic repair source changed')
                mapped, base_report = self.validator.parse(ShotPlanDraft, base, project, scene=scene)
                if mapped is None:
                    raise ValueError("Terminal repair requires a mappable preserved plan")
                request_format = self.adapter.response_format(TerminalRepairDraft)
                request_format["json_schema"]["schema"]["$defs"]["TerminalShotRepair"]["properties"]["shot_index"]["const"] = len(mapped.shots) - 1
                if invocation.semantic_revision and content_hash(request_format) != invocation.semantic_revision.request_schema_hash:
                    raise ValueError("Authorized semantic repair schema changed")
                prompt = terminal_repair_prompt(original, base, scene,
                    mapped.shots[-1].expected_state_after, report if report.issues else base_report)
                request_instruction += ('\nFor this targeted repair request ONLY, return TerminalRepairDraft '
                    'as supplied by response_format, NOT ShotPlanDraft. Core retains all uneditable fields '
                    'and validates the complete merged ShotPlanDraft. Treat required_terminal_delta as '
                    'authoritative per-entity terminal facts. Realize the change from the canonical '
                    'shot start in performance/action and frames; do not transpose start/end or entity targets.')
            package = PromptPackage(compiler_id="role_runtime", compiler_version="1",
                                    positive_prompt=prompt)
            request = ProviderRequest(provider_request_id=f"{invocation.invocation_id}_{index}",
                provider_id=self.provider.provider_id, generation_request=GenerationRequest(
                    job_id=invocation.invocation_id, task="structured_text",
                    strategy=GenerationStrategy(strategy_type=GenerationStrategyType.STRUCTURED_TEXT,
                                                reason="Schema-constrained role output"),
                    prompt_package=package, input_artifact_ids=context.source_ids,
                    requested_output_type="structured_text", resource_class=ResourceClass.LIGHT,
                    parameters=CompletionOptions(system_prompt=request_instruction,
                        response_format=request_format,
                        max_tokens=policy.max_output_tokens, inference_policy=policy,
                        retry_uncertain=result.failure_code == "remote_completion_uncertain").model_dump(mode="json")))
            metrics = InferenceMetrics(request_id=request.provider_request_id,
                role_id=invocation.role_id, scene_id=invocation.scene_id,
                context_chars=len(original), prompt_chars=len(prompt), schema_chars=len(json.dumps(request_format, ensure_ascii=False)),
                input_token_estimate=math.ceil((len(prompt) + len(request_instruction) + len(json.dumps(request_format, ensure_ascii=False))) / 4),
                max_output_tokens=policy.max_output_tokens, read_timeout=policy.read_timeout,
                total_timeout=policy.total_timeout,
                inactivity_timeout=policy.inactivity_timeout if policy.stream else policy.read_timeout,
                streaming=policy.stream, started_at=utc_now())
            result.inference_records.append(metrics)
            self._emit(EventType.PROVIDER_REQUEST_STARTED, invocation, metrics.model_dump(mode="json"))
            if on_attempt:
                on_attempt(result)
            response = await self.provider.submit(request)
            metrics.ended_at = utc_now()
            metrics.latency_seconds = response.latency_seconds
            metrics.finish_reason = response.metadata.get("finish_reason")
            metrics.failure_phase = response.metadata.get("failure_phase")
            metrics.provider_outcome = response.metadata.get("provider_outcome") or (
                "success" if response.success else response.error_type.value)
            usage = response.metadata.get("usage", {})
            if isinstance(usage, dict):
                metrics.prompt_tokens = usage.get("prompt_tokens")
                metrics.completion_tokens = usage.get("completion_tokens")
            self._emit(EventType.PROVIDER_REQUEST_COMPLETED, invocation, {
                **metrics.model_dump(mode="json"), "success": response.success,
                "error_type": response.error_type})
            if not response.success:
                result.failure_code = response.error_type.value
                if on_attempt:
                    on_attempt(result)
                raise ProviderFailure(response.error_message or "Provider request failed", response.error_type,
                                      response.retryable)
            result.failure_code = None
            raw = response.metadata.get("content", "")
            self._emit(EventType.ROLE_OUTPUT_RECEIVED, invocation, {"request_id": request.provider_request_id})
            candidate_raw = raw if isinstance(raw, str) else ""
            try:
                if base:
                    candidate_raw = merge_terminal_repair(base, candidate_raw)
                output, report = self.validator.parse(target, candidate_raw, project, scene=scene)
            except ValueError:
                output = None
                candidate_raw = base
                report = ValidationReport(issues=[OutputIssue(code=OutputErrorCode.SCHEMA_INVALID,
                    path="shot_repairs", message="Return one valid TerminalRepairDraft for the final shot, including performances, local_state_delta and frame_planning.")])
            metrics.validation_result = "passed" if report.valid else "failed"
            result.served_model = response.metadata.get("served_model")
            usage = response.metadata.get("usage", {})
            result.attempts.append(RoleAttempt(request_id=request.provider_request_id,
                prompt_hash=content_hash(prompt), latency_seconds=response.latency_seconds,
                raw_output=raw if isinstance(raw, str) else None, request_prompt=prompt,
                token_usage={k: v for k, v in usage.items() if isinstance(v, int)} if isinstance(usage, dict) else {},
                validation=report))
            if report.valid:
                result.output = output.model_dump(mode="json")
                result.failure_code = None
                result.pending_output = None
                self._emit(EventType.ROLE_OUTPUT_VALIDATED, invocation, {"target_schema": target.__name__,
                    "repair_attempts": index, "context_hash": context.context_hash,
                    "metrics": metrics.model_dump(mode="json")})
                return result
            self._emit(EventType.ROLE_OUTPUT_VALIDATION_FAILED, invocation, {
                "attempt": index, "issues": [i.model_dump(mode="json") for i in report.issues],
                "metrics": metrics.model_dump(mode="json")})
            result.pending_output = candidate_raw
            if base:
                result.terminal_repair_base = candidate_raw
            if revision and scene and output is not None and self._state_scope_conflict(output, scene):
                result.failure_code = "CONTRACT_STATE_SCOPE_CONFLICT"
                if on_attempt:
                    on_attempt(result)
                raise RoleOutputInvalid(result)
            if on_attempt:
                on_attempt(result)
            prompt = self.repairer.prompt(original, raw, report, response_format["json_schema"])
        result.failure_code = "ROLE_OUTPUT_INVALID"
        raise RoleOutputInvalid(result)

    @staticmethod
    def _state_scope_conflict(output, scene):
        if any(chain.initial_state.model_dump() != scene.initial_state.model_dump()
               for chain in output.continuity_chains):
            return True
        return any(set(getattr(state, field)) - set(allowed)
            for shot in output.shots for state in (shot.state_before, shot.expected_state_after)
            for field, allowed in (("character_states", scene.character_ids),
                                   ("prop_states", scene.prop_ids), ("location_states", [scene.location_id])))

    def _emit(self, kind, invocation, payload):
        self.event_bus.emit(EventEnvelope(event_type=kind, project_id=invocation.project_id,
            trace_id=self.trace_id, node_id=invocation.node_id, job_id=invocation.invocation_id,
            payload={"role_id": invocation.role_id.value, "scene_id": invocation.scene_id, **payload}))
