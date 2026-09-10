import json
from types import SimpleNamespace as NS
import pytest
from movie_agent.artifacts import LocalArtifactStore
from movie_agent.domain import ProviderResult
from movie_agent.quality.cinematic import evaluate_cinematic
from tests.test_p5_quality import quality_inputs,shot


class Model:
    provider_id='test-reasoning'
    calls=0
    def __init__(self,evidence_id,*,finish='stop',foreign=False):
        self.evidence_id=evidence_id;self.finish=finish;self.foreign=foreign
    async def submit(self,request):
        self.calls+=1
        self.last_system_prompt=request.generation_request.parameters['system_prompt']
        return ProviderResult(provider_request_id=request.provider_request_id,success=True,metadata={
            'finish_reason':self.finish,'content':json.dumps({'score':.9,'summary':'motivated reaction shot',
                'evidence_ids':['invented' if self.foreign else self.evidence_id],'action':'KEEP',
                'rationale':'observed placement supports the choice','major_continuity_break':False,
                'dimensions':{'composition':.9,'continuity':None}})})


@pytest.mark.asyncio
@pytest.mark.parametrize('finish,foreign',[('stop',False),('length',False),('stop',True)])
async def test_cinematic_binds_observations_caches_and_rejects_invalid_evidence(tmp_path,finish,foreign):
    artifact,inspections,_=quality_inputs()
    model=Model(inspections[0].result_id,finish=finish,foreign=foreign)
    engine=NS(artifact_store=LocalArtifactStore(tmp_path),llm_provider=model,
        media_runtime=NS(inspections=inspections,runtime_coordinator=None))
    data=NS(model_dump=lambda **_: {'intent':'test'})
    project=NS(project_id='p',story_bible=data,creative_direction=data,shots=[],brief=NS(output_language='en'))
    if finish!='stop' or foreign:
        with pytest.raises(RuntimeError,match='generation_failed'):
            await evaluate_cinematic(engine,project,shot=shot(),artifact=artifact)
        diagnostic=engine.artifact_store.list_all()[0]
        assert engine.artifact_store.read_structured(diagnostic)['response_received'] is True
        return
    result=await evaluate_cinematic(engine,project,shot=shot(),artifact=artifact)
    assert result.passed and result.target_artifact_version==2
    assert 'project output language: en' in model.last_system_prompt
    again=await evaluate_cinematic(engine,project,shot=shot(),artifact=artifact)
    assert again.evaluation_id==result.evaluation_id and model.calls==1
