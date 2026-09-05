"""Deterministic role output fixtures based on supplied context, never network calls."""

import json
from movie_agent.domain import (
    CreativeDirection, StoryBible, VisualBible, Screenplay, ScreenplayScene, Character, Location,
    ScenePlan, Scene, ShotPlan, Shot, ShotNarrative, CameraSpec, ShotSize, LightingSpec,
    ContinuityChain, ProviderResult,
)
from movie_agent.providers.mock import MockProvider
from movie_agent.providers.base import LLMProvider
from movie_agent.orchestration.runtime.cinematographer_drafts import (
    FramePlanningDraft, ShotDraft, ShotLocalStateDraft, ShotPlanDraft,
)


class FakeReasoningProvider(MockProvider, LLMProvider):
    """Typed role fake for application and resume tests."""

    def __init__(self, fail_schema=None):
        super().__init__("fake-reasoning")
        self.calls = []
        self.fail_schema = fail_schema

    async def submit(self, request):
        parameters = request.generation_request.parameters
        name = parameters["response_format"]["json_schema"]["name"]
        self.calls.append(name)
        if name == self.fail_schema:
            return ProviderResult(provider_request_id=request.provider_request_id, success=False,
                error_type="unavailable", retryable=True, error_message="intentional disconnect")
        context = json.loads(request.generation_request.prompt_package.positive_prompt)
        if "original_task" in context:
            context = json.loads(context["original_task"])
        brief = context["brief"]
        hard = brief["user_constraints"] + brief["must_preserve"]
        commitments = {"preserved_constraints": hard,
            "immutable_facts": context.get("story_bible", {}).get("immutable_facts", [])}
        if name == "CreativeDirection":
            output = CreativeDirection(premise_expansion="A person hears a signal", emotional_arc="Fear to courage",
                tone="intimate", preserved_user_constraints=hard)
        elif name == "StoryBible":
            output = StoryBible(synopsis="A person chooses truth", immutable_facts=brief["must_preserve"] + brief["world_rules"])
        elif name == "Screenplay":
            output = Screenplay(title=brief["title"], characters=[Character(character_id="person", name="Person")],
                locations=[Location(location_id="room", name="Room")], scenes=[ScreenplayScene(scene_id="scene_a",
                    location_id="room", title="Signal", action=["A person listens"], character_ids=["person"])], **commitments)
        elif name == "VisualBible":
            output = VisualBible(style_statement="Quiet realism", prohibited_visuals=brief["prohibited_elements"])
        elif name == "ScenePlan":
            output = ScenePlan(scenes=[Scene(scene_id="scene_a", title="Signal", purpose="Listen",
                location_id="room", time_description="night", character_ids=["person"])], **commitments)
        else:
            output = ShotPlanDraft(shots=[ShotDraft(duration_seconds=context["scene_duration_budget"],
                narrative=ShotNarrative(purpose="Listen", beat="The signal stops"),
                camera=CameraSpec(shot_size=ShotSize.WIDE), lighting=LightingSpec(setup="practical"),
                frame_planning=FramePlanningDraft(first_frame_description="Canonical opening",
                    last_frame_description="Resolved ending"),
                local_state_delta=ShotLocalStateDraft())], **commitments)
        return ProviderResult(provider_request_id=request.provider_request_id, success=True,
            metadata={"content": output.model_dump_json(), "served_model": "fake", "usage": {"total_tokens": 40}})
