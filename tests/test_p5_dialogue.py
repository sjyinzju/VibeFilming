from types import SimpleNamespace as NS
import pytest
from movie_agent.media.dialogue import extract_dialogue,DialogueTimingReview
from movie_agent.providers.registry import MediaProviderSettings


def test_multishot_scene_allows_silent_cutaway_but_never_drops_screenplay_dialogue():
    character=NS(character_id='ella',name='Ella')
    line=NS(character_id='ella',text='留在这里。')
    silent=NS(shot_id='silent',scene_id='scene',narrative=NS(dialogue=[]),performances=[],duration_seconds=6)
    spoken=NS(shot_id='spoken',scene_id='scene',narrative=NS(dialogue=['Ella: 留在这里。']),performances=[],duration_seconds=8)
    project=NS(project_id='p',characters=[character],brief=NS(output_language='zh'),shots=[silent,spoken],
        screenplay=NS(scenes=[NS(scene_id='scene',dialogue=[line])]))
    cues=extract_dialogue(project,{},MediaProviderSettings())
    assert len(cues)==1 and cues[0].shot_id=='spoken'
    spoken.narrative.dialogue=[]
    with pytest.raises(DialogueTimingReview,match='explicit shot assignment'):
        extract_dialogue(project,{},MediaProviderSettings())
