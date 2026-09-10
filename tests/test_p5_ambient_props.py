from tests.test_p2a_cinematographer_drafts import scene2_fixture,draft
from movie_agent.orchestration.runtime.cinematographer_drafts import CinematographerDraftMapper,ShotLocalStateDraft


def test_unchanged_ambient_held_prop_is_inheritance_but_relationship_changes_are_rejected():
    project,scene=scene2_fixture();scene.prop_ids=[]
    def validate(held):
        return CinematographerDraftMapper().validate_local(draft(ShotLocalStateDraft(
            character_updates=[{'character_id':'MARA','held_prop_ids':held}])),project,scene)
    assert validate(['OVERRIDE']).valid
    assert validate(None).valid
    assert not validate([]).valid
    assert not validate(['OVERRIDE','CONSOLE']).valid
    assert not validate(['UNKNOWN']).valid
