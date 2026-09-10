from types import SimpleNamespace as NS
from movie_agent.domain import ScenePlan
from movie_agent.orchestration.runtime.runner import StructuredOutputAdapter


def test_scene_state_maps_accept_only_canonical_keys_and_matching_value_ids():
    project=NS(characters=[NS(character_id='ella')],locations=[NS(location_id='ground')],
        props=[],screenplay=NS(scenes=[NS(scene_id='scene1')]))
    schema=StructuredOutputAdapter().response_format(ScenePlan,project=project)['json_schema']['schema']
    state=schema['$defs']['ContinuityState']['properties']
    assert state['character_states']['additionalProperties'] is False
    assert set(state['character_states']['properties'])=={'ella'}
    assert state['character_states']['properties']['ella']['properties']['character_id']['const']=='ella'
    assert state['prop_states']['properties']=={} and state['prop_states']['additionalProperties'] is False
    assert 'propertyNames' not in str(schema)
