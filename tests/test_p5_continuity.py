from movie_agent.cinematic.continuity import validate_transition
from movie_agent.domain import ContinuityState,CharacterState,Vector3
from tests.test_p5_quality import shot


def test_vector_schema_metadata_is_not_a_physical_jump_but_coordinates_and_space_are():
    previous=ContinuityState(character_states={'c':CharacterState(character_id='c',
        position=Vector3(schema_version='1.0',x=0,y=.05,z=0))})
    target=shot()
    target.state_before=ContinuityState(character_states={'c':CharacterState(character_id='c',
        position=Vector3(schema_version='1.0.0',x=0,y=.05,z=0))})
    assert validate_transition(previous,target)==[]
    target.state_before.character_states['c'].position.y=.5
    assert validate_transition(previous,target)
    target.state_before.character_states['c'].position.y=.05
    target.state_before.character_states['c'].position.coordinate_space='world'
    assert validate_transition(previous,target)
