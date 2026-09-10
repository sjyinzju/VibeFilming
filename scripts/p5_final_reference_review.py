"""Last bounded reference revision, with four source-preserving edits and unchanged controls."""
import json
from pathlib import Path
from movie_agent.services.hero_production import HeroMovieProduction
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.domain import WorkflowNodeStatus
from movie_agent.storage.projects import LocalProjectRepository

root=Path('workspace/p5-hero-film');pid=(root/'project-id.txt').read_text().strip()
assert LocalProjectRepository(root).get(pid).status.value!='running'
state=json.loads(Path('workspace/_resource_runtime/spark-state.json').read_text())
assert not [l for l in state['leases'] if l['released_at'] is None]
engine=HeroMovieProduction(root/pid,None,media_settings=MediaProviderSettings())
project,graph=engine._restore();engine.current_project,engine.current_production=project,graph
assert not engine.artifact_store.get('p5_reference_repair_plan')
assert not engine.existing_videos(project)
instructions={
 'CHAR_LYNNE_001':(
    'Preserve the same single person, apparent age, face shape, pale skin, full-body framing and cool cyan setting. '
    'Lower the hood behind his head so his existing face and hair are unobscured and more clearly lit. '
    'Keep the pale blue full-body suspension suit and inner clothing intact; make its outer fabric subtly translucent '
    'and its cuffs softly luminous blue. Show restrained fear and curiosity in his eyes. No text, no other people.',
    'One full-body pale-skinned youthful person, visible unobscured face and lowered hood, wearing an intact pale blue '
    'suspension suit with a subtly translucent outer layer over clothing and faint blue luminous cuffs. '
    'Cool cyan cinematic setting, readable face and costume, no text or extra people.'),
 'LOC_SKY_CITY_001':(
    'Keep the same empty futuristic corridor, smooth curved walls, camera perspective and cool cyan silver lighting. '
    'Replace the central flush floor with a clearly detached horizontal suspension platform hovering about half a '
    'meter above a lower rough base. Make the near platform edge and visible air gap unambiguous. '
    'Retain a coherent walkable platform surface. No people, spacecraft, text or split-screen.',
    'An empty clean futuristic interior with smooth curved walls, cool cyan/silver illumination, and a readable '
    'detached central hovering platform above a lower base. The near edge and air gap make the suspension visible. '
    'Single coherent cinematic environment, no people, spacecraft or text.'),
 'LOC_FALL_ZONE_001':(
    'Preserve this empty dusty city canyon, tall fixed buildings, rough rust-colored ground, haze and camera composition. '
    'Remove both flying saucer-shaped objects completely, leaving continuous hazy air between the buildings. '
    'Strengthen natural warm amber illumination on the dusty foreground while keeping distant upper architecture cooler. '
    'Do not add people, vehicles, spacecraft, text, logos or extra structures.',
    'An empty city-ground boundary with rough ochre/rust earth, warm dusty foreground illumination, heavy shadows '
    'and tall cooler distant architecture. No people, flying discs, spacecraft, vehicles or text. '
    'A coherent grounded environment with clear material and palette contrast.'),
 'VB_LYNNE_ELLA_001':(
    'Convert this into an environment-only cinematic palette and material reference. Remove the central hooded person '
    'and all human figures, preserving the city canyon perspective. Retain cold cyan silver high-tech architecture '
    'above, contrasting with warm ochre rust-colored dusty coarse earth below. Make a smooth raised platform edge '
    'meet the rough ground boundary in the midground. Keep cinematic depth, diffuse cold light and warm dusty shadows. '
    'No people, lettering, logos, watermarks or split-screen.',
    'Environment-only cinematic STYLE reference: cold cyan/silver technology above and warm ochre/rust dusty '
    'coarse ground below, with a smooth platform edge at the rough-ground boundary. Strong warm/cool material '
    'contrast and depth, no people or text. This reference establishes palette, lighting and materials, not faces.'),
}
edits={}
for subject,(instruction,expected) in instructions.items():
    artifact=engine.artifact_store.get('identity_'+subject,1)
    assert artifact
    edits[subject]={'source':engine.pin(artifact).model_dump(mode='json'),'instruction':instruction,'expected_final':expected}
for subject in [*instructions,'CHAR_ELLA_001','PROP_SUSPENDER_001']:
    assert sum(r['kind']=='vlm_inspection' and r['subject']=='identity_'+subject for r in engine.media_runtime.quality_ledger.records())==2
engine.commit_quality('p5_reference_repair_plan',{
    'authorization':'P5 autonomous bounded local repair', 'edits':edits,
    'unchanged_controls':['CHAR_ELLA_001','PROP_SUSPENDER_001'],
    'reasoning_service_evidence':'vlm-reasoning-service-change.json',
    'final_inspection_revision':3,'max_edits_each':1,'human_approved':False,
    'scope_note':'Style reference describes color/light/material only; character identity remains in separate character references.'},
    'p5_reference_repair_plan')
affected={'asset_planning','storyboard_planning','shot_production','technical_qc','visual_semantic_critic',
    'cinematic_critic','repair_accept','audio_post','rough_cut','full_film_review','final_gate','final_render'}
for node in graph.graph.nodes:
    if node.node_id in affected:
        node.status=WorkflowNodeStatus.PENDING;node.progress=0;node.completed_at=None
project.canonical_state.completed_node_ids=[n.node_id for n in graph.graph.nodes if n.status==WorkflowNodeStatus.SUCCEEDED]
engine._durable_media_checkpoint()
print('Four exact-source edit instructions saved; final third inspections scheduled; completed TTS/music preserved.')
