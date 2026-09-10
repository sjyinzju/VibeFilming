"""P5 policy extension of the existing production DAG, media runtime and checkpoints."""
from hashlib import sha256

from movie_agent.domain import (ArtifactType, GenerationJob, PromptPackage, QualityProfile,
    ResourceClass, Provenance, WorkflowNodeStatus, HumanGateType, EvaluationLayer)
from movie_agent.media import (ImageGenerationRequest, ImageGenerationMode, ImagePurpose, MediaReference,
    ReferenceType, MediaCapabilityRequirement, VisionInspectionRequest, VisionInspectionProfile as VP,
    VisionDecision, Timeline, TimelineClip, VideoTrack)
from movie_agent.quality.budget import QualityBudgetLedger
from movie_agent.quality.references import (ReferenceIdentitySet, IdentityReference, ReferenceRole,
    ReferenceResolver)
from movie_agent.quality.reports import aggregate_shot, aggregate_film, ShotQualityStatus, QualityThresholds
from movie_agent.quality.cinematic import evaluate_cinematic, film_proxy
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.media.compilers import GenericVideoPromptCompiler


class ProductionSoundCompiler(GenericVideoPromptCompiler):
    def __init__(self):self.english={}
    def compile(self,*args,**kwargs):
        kwargs['has_authoritative_dialogue']=True
        result=super().compile(*args,**kwargs)
        return result.model_copy(update={'positive_prompt':self.english.get(result.positive_prompt,result.positive_prompt)})


class HeroMovieProduction(ReasoningMovieProduction):
    """Opt-in only. No existing acceptance project is upgraded implicitly."""
    def __init__(self,*args,thresholds=None,quality_budget=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.thresholds=thresholds or QualityThresholds()
        self.media_runtime.quality_ledger=QualityBudgetLedger(self.artifact_store,quality_budget)
        self.video_prompt_compiler=ProductionSoundCompiler()
        self.continue_independent_on_review=True

    def commit_quality(self, identity, content, purpose):
        prior=self.artifact_store.get(identity)
        if prior and self.artifact_store.read_structured(prior)==content: return prior
        result=self.artifact_store.create_structured(identity,content,metadata={'purpose':purpose},
            provenance=Provenance(project_id=self.current_project.project_id,tool='p5_quality_core'))
        self.artifact_store.select(result.artifact_id,result.version)
        self._durable_media_checkpoint()
        return result

    def pin(self, artifact, kind=ReferenceType.SOURCE_IMAGE):
        with self.binary_store.open(artifact.uri) as stream: digest=sha256(stream.read()).hexdigest()
        return MediaReference(reference_id=f'{kind.value}_{artifact.artifact_id}_v{artifact.version}',
            reference_type=kind,artifact_id=artifact.artifact_id,version=artifact.version,sha256=digest)

    def pack(self):
        saved=self.artifact_store.get('reference_identity_set')
        return ReferenceIdentitySet.model_validate(self.artifact_store.read_structured(saved)) if saved else ReferenceIdentitySet()

    async def _screenplay(self,project):
        await super()._screenplay(project)

    async def _shot_planning(self,project):
        await super()._shot_planning(project)
        if not project.scenes or not project.shots:
            raise ValueError('Production planning requires scenes and purposeful shots')
        risks=[]
        for shot in project.shots:
            shot.retry_budget=1; shot.quality_profile=self.thresholds.profile
            if (shot.narrative.dialogue or any(p.dialogue for p in shot.performances)) and 'close' in shot.camera.shot_size.value:
                risks.append({'shot_id':shot.shot_id,'risk':'visible_lip_sync_risk','status':'human_required'})
        self.commit_quality('dialogue_staging_risks',risks,'quality_risks')

    def shot_references(self,project,shot,*,repair_context=None):
        scene=next(s for s in project.scenes if s.scene_id==shot.scene_id)
        resolved=ReferenceResolver().resolve(shot,scene,project.visual_bible,self.pack(),store=self.artifact_store,repair_context=repair_context)
        self.commit_quality('references_'+shot.shot_id,resolved.model_dump(mode='json'),'shot_reference_resolution')
        return resolved.references

    def _media_references(self,project,shot,*,repair_context=None):
        saved=self.artifact_store.get('frame_bindings_'+shot.shot_id)
        frames=[MediaReference.model_validate(r) for r in self.artifact_store.read_structured(saved)] if saved else []
        return [*frames,*self.shot_references(project,shot,repair_context=repair_context)]

    def _prepare_video_request(self,project,shot,request):
        from movie_agent.media.video_conditioning import bind_reviewed_boundaries
        return bind_reviewed_boundaries(request,self.artifact_store)

    async def inspect_frame(self,project,artifact,*,shot=None,refs=None,requirements=None):
        pin=self.pin(artifact)
        profiles=[VP.IMAGE_QUALITY,VP.ARTIFACT_DETECTION,VP.PROMPT_ALIGNMENT]
        if any(r.reference_type==ReferenceType.CHARACTER for r in refs or []): profiles.append(VP.CHARACTER_IDENTITY)
        if any(r.reference_type==ReferenceType.LOCATION for r in refs or []): profiles.append(VP.SCENE_CONSISTENCY)
        identity='frame_inspection_'+artifact.artifact_id+'_v'+str(artifact.version)
        request=VisionInspectionRequest(job_id=identity,project_id=project.project_id,
            shot_id=shot.shot_id if shot else None,scene_id=shot.scene_id if shot else None,
            image_artifact_id=artifact.artifact_id,target_artifact_version=artifact.version,target_sha256=pin.sha256,
            reference_assets=refs or [],expected_shot=shot,expected_requirements=requirements or [],
            profiles=profiles,output_artifact_id=identity,output_language=project.brief.output_language,retry_budget=2)
        return await self.media_runtime.inspect(GenerationJob(job_id=identity,project_id=project.project_id,
            node_id='storyboard_planning',task='vision',resource_class=ResourceClass.LIGHT,idempotency_key=identity),request)

    async def image(self,project,identity,prompt,*,source=None,references=None,conditioning=None,purpose=ImagePurpose.FIRST_FRAME,shot=None,repair=False):
        from movie_agent.quality.budget import fingerprint
        from movie_agent.quality.visual_prompts import english_visual_prompt
        from movie_agent.media.frame_planning import reference_canvas
        width,height=reference_canvas(project.brief.aspect_ratio)
        prompt=await english_visual_prompt(self,project,prompt)
        signature=fingerprint({'source':source.model_dump(mode='json',exclude={'reference_id'}) if source else None,
            'prompt':prompt,'purpose':purpose.value,'dimensions':[width,height],
            'seed':int(sha256(identity.encode()).hexdigest()[:8],16),'compiler':'p5-reference-1'})
        if references:
            signature=fingerprint({'base':signature,'references':[r.model_dump(mode='json',exclude={'reference_id'}) for r in references],
                'conditioning':conditioning})
        receipt_id='image_receipt_'+signature
        receipt=self.artifact_store.get(receipt_id)
        if receipt:
            data=self.artifact_store.read_structured(receipt)
            cached=self.artifact_store.get(data['artifact_id'],data['version'])
            if cached is None or self.pin(cached).sha256!=data['sha256']:
                raise ValueError('Cached image exact version/hash unavailable')
            return cached
        existing=self.artifact_store.get(identity)
        if existing and not repair:
            raise ValueError('Image input/config changed; explicit reviewed revision required')
        version=len(self.artifact_store.list_versions(identity))+1
        mode=ImageGenerationMode.IMAGE_EDIT if source or references else ImageGenerationMode.TEXT_TO_IMAGE
        request=ImageGenerationRequest(job_id=f'p5-image:{identity}:v{version}:{signature[:16]}',project_id=project.project_id,
            scene_id=shot.scene_id if shot else None,shot_id=shot.shot_id if shot else None,
            mode=mode,purpose=purpose,source_image=source,references=references or [],reference_conditioning=conditioning,output_artifact_id=identity,
            width=width,height=height,aspect_ratio=project.brief.aspect_ratio,seed=int(sha256(identity.encode()).hexdigest()[:8],16),
            quality_profile=QualityProfile.SHOWCASE,resource_class=ResourceClass.MEDIUM,
            required_capabilities=[MediaCapabilityRequirement(capability=mode.value)]+(
                [MediaCapabilityRequirement(capability='reference_sheet' if conditioning=='reference_sheet' else 'multi_reference')] if references else []),
            provider_parameters={'preserve':['All source identity, wardrobe and geometry not explicitly changed by the instruction.'],
                'change':['Only the visual changes specified by the instruction.']} if source else {},
            prompt_package=PromptPackage(compiler_id='p5-reference',compiler_version='1',positive_prompt=prompt))
        artifact=await self.media_runtime.generate_image(GenerationJob(job_id=request.job_id,project_id=project.project_id,
            node_id='storyboard_planning',task='frame',resource_class=ResourceClass.MEDIUM,idempotency_key=request.job_id,
            provenance=Provenance(project_id=project.project_id,shot_id=shot.shot_id if shot else None)),request,artifact_type=ArtifactType.FRAME)
        self.commit_quality(receipt_id,self.pin(artifact).model_dump(mode='json'),'image_input_config_receipt')
        return artifact

    async def _asset_planning(self,project):
        await super()._asset_planning(project)
        bible=project.visual_bible
        constraints=[bible.style_statement,*bible.palette,*bible.lighting_rules,*bible.continuity_rules]
        descriptors=[]
        for c in project.characters:
            descriptors.append((c.character_id,ReferenceType.CHARACTER,ImagePurpose.CHARACTER_PLATE,
                [ReferenceRole.PORTRAIT,ReferenceRole.SILHOUETTE,ReferenceRole.WARDROBE],
                f'Canonical full-body character plate with clearly readable face and entire costume, one person only. '
                f'{c.identity_description}. '+ '; '.join(c.appearance_constraints)))
        for loc in project.locations:
            descriptors.append((loc.location_id,ReferenceType.LOCATION,ImagePurpose.LOCATION_PLATE,
                [ReferenceRole.ENVIRONMENT,ReferenceRole.LIGHTING,ReferenceRole.LANDMARK],
                f'Canonical empty wide environment, no people. {loc.description}. '+'; '.join(loc.immutable_features)))
        for prop in project.props:
            if prop.continuity_critical:
                descriptors.append((prop.prop_id,ReferenceType.PROP,ImagePurpose.STYLE_FRAME,[ReferenceRole.PROP],
                    f'Canonical isolated story prop, clearly readable shape and material. {prop.name}: {prop.description}'))
        descriptors.append((bible.visual_bible_id,ReferenceType.STYLE,ImagePurpose.STYLE_FRAME,[ReferenceRole.STYLE],
            'Canonical cinematic style and palette reference, no text. '+'; '.join(constraints)))
        images=[]
        from movie_agent.quality.visual_prompts import english_visual_prompt
        compiled={subject:await english_visual_prompt(self,project,description+'\nConditional world context:\n'+'; '.join(constraints),
                    scope={'subject_id':subject,'reference_type':kind.value,'target_description':description})
                  for subject,kind,purpose,roles,description in descriptors}
        repair_artifact=self.artifact_store.get('p5_reference_repair_plan')
        repairs=self.artifact_store.read_structured(repair_artifact)['edits'] if repair_artifact else {}
        for subject,kind,purpose,roles,description in descriptors:
            artifact=await self.image(project,'identity_'+subject,compiled[subject],purpose=purpose)
            ancestry=[]
            if subject in repairs:
                edit=repairs[subject];source=MediaReference.model_validate(edit['source'])
                if self.pin(artifact).sha256!=source.sha256 or artifact.version!=source.version:
                    raise ValueError('Reference repair source differs from reviewed original')
                artifact=await self.image(project,artifact.artifact_id,edit['instruction'],source=source,purpose=purpose,repair=True)
                ancestry=[source]
            images.append((subject,kind,roles,description,artifact,ancestry))
        pack=ReferenceIdentitySet(canonical_constraints=constraints)
        for subject,kind,roles,description,artifact,ancestry in images:
            expected=repairs.get(subject,{}).get('expected_final',compiled[subject])
            inspection=None
            try:
                inspection=await self.inspect_frame(project,artifact,requirements=[expected,
                    'This is the initial canonical reference for '+subject+'. Evaluate only the requested subject and '
                    'visible image quality. Other named characters and other locations are not required in this plate. '
                    'The character name is a label being assigned to this reference, not a pre-existing face to recognize. '
                    'Judge the actual full image framing; distinguish observed defects from details that cannot be verified.'])
            except Exception as error:
                self.commit_quality('reference_review_error_'+subject,{
                    'target':self.pin(artifact).model_dump(mode='json'),'reason':str(error),
                    'status':'human_review','human_approved':False},'reference_review_error')
            pin=self.pin(artifact)
            for role in roles:
                pack.references.append(IdentityReference(artifact_id=pin.artifact_id,version=pin.version,sha256=pin.sha256,
                    reference_type=kind,reference_role=role,subject_id=subject,
                    selected=inspection is not None and inspection.decision==VisionDecision.PASS,
                    quality_status=inspection.decision if inspection else VisionDecision.HUMAN_REVIEW,
                    inspection_result_id=inspection.result_id if inspection else None,
                    score=min(s.score for s in inspection.scores) if inspection else None,created_from=ancestry))
        self.commit_quality('reference_identity_set',pack.model_dump(mode='json'),'reference_identity_set')

    async def _storyboard_planning(self,project):
        from movie_agent.media.compilers import GenericImagePromptCompiler
        from movie_agent.quality.visual_prompts import english_visual_prompt
        generated=[]; prepared=[]
        for shot in project.shots:
            try:
                refs=self.shot_references(project,shot)
                environment=next((r for r in refs if r.reference_type==ReferenceType.LOCATION),None)
                source=(next((r for r in refs if r.reference_type==ReferenceType.CHARACTER),environment)
                        if shot.performances else environment)
                person_source=source is not None and source.reference_type==ReferenceType.CHARACTER
                source=source.model_copy(update={'reference_type':ReferenceType.SOURCE_IMAGE}) if source else None
                prompt=GenericImagePromptCompiler().compile(shot,ImagePurpose.FIRST_FRAME,refs).positive_prompt
                prompt+=('\nPreserve exact identity, apparent age, face, hair and wardrobe of source person. '
                         if person_source else '\nPreserve source architecture, landmarks, material and palette. ')
                prompt+='Recompose into the specified shot and location. '+'; '.join(self.pack().canonical_constraints)
                last_prompt=('Preserve the same people, face, costume, location, lighting, lens and camera framing. '
                    'Use intended_change below as the visible final pose/state; current_state is comparison only. '
                    'Show the end of this action: '+shot.narrative.action_summary+'\n'+prompt)
                prepared.append((shot,refs,source,await english_visual_prompt(self,project,prompt),
                    await english_visual_prompt(self,project,last_prompt)))
            except Exception as error:self.block_shot(shot,'frame_prompt_compilation',str(error))
        for shot,refs,source,prompt,last_prompt in prepared:
            try:
                first=await self.image(project,'frame_'+shot.shot_id+'_first',prompt,source=source,shot=shot)
                last=await self.image(project,'frame_'+shot.shot_id+'_last',last_prompt,
                    source=self.pin(first),purpose=ImagePurpose.LAST_FRAME,shot=shot)
                shot.frame_anchors.first_frame.source_artifact_id=first.artifact_id
                shot.frame_anchors.last_frame.source_artifact_id=last.artifact_id
                generated.append((shot,refs,first,last))
            except Exception as error:
                self.block_shot(shot,'frame_generation',str(error))
        for shot,refs,first,last in generated:
            results=[]
            checked=[]
            for artifact in (first,last):
                try:
                    result=await self.inspect_frame(project,artifact,shot=shot,refs=refs,
                        requirements=['Compare exact recurring identity, wardrobe, location, style and shot intent. '
                                      'Do not claim action completion or motion from a single still image.'])
                    if result.decision in {VisionDecision.REPAIR,VisionDecision.REGENERATE} and result.issues:
                        # One source-preserving edit, then recheck. Ledger caps each image revision chain.
                        reason='; '.join(i.message for i in result.issues if i.severity.value in {'major','critical'})
                        if reason:
                            artifact=await self.image(project,artifact.artifact_id,
                                'Preserve all correct identity, clothing, setting and framing. Repair only: '+reason,
                                source=self.pin(artifact),shot=shot,repair=True)
                            result=await self.inspect_frame(project,artifact,shot=shot,refs=refs,
                                requirements=['Verify repair and identity against selected canonical references.'])
                    results.append(result); checked.append(artifact)
                except Exception as error:
                    self.block_shot(shot,'frame_inspection',str(error))
            if len(checked)==2: first,last=checked
            passed=len(results)==2 and all(r.decision==VisionDecision.PASS for r in results)
            self.commit_quality('frame_gate_'+shot.shot_id,{'passed':passed,'shot_id':shot.shot_id,
                'inspections':[r.result_id for r in results],
                'frames':[self.pin(first).model_dump(mode='json'),self.pin(last).model_dump(mode='json')]},'frame_gate')
            if passed:
                self._select(project,first.artifact_id,first.version)
                self._select(project,last.artifact_id,last.version)
                self.commit_quality('frame_bindings_'+shot.shot_id,[self.pin(first,ReferenceType.FIRST_FRAME).model_dump(mode='json'),
                    self.pin(last,ReferenceType.LAST_FRAME).model_dump(mode='json')],'exact_frame_bindings')
            else: self.block_shot(shot,'frame_gate','Frame identity/scene quality did not pass; H3 not dispatched')

    def block_shot(self,shot,stage,reason):
        self.commit_quality('human_review_'+shot.shot_id,{'shot_id':shot.shot_id,'stage':stage,
            'reason':reason,'status':'human_review','human_approved':False},'p5_shot_human_review')

    async def _shot_production(self,project):
        eligible=[s for s in project.shots if self.artifact_store.get('frame_gate_'+s.shot_id)
                  and self.artifact_store.read_structured(self.artifact_store.get('frame_gate_'+s.shot_id))['passed']]
        await self.prepare_video_prompts(project,eligible)
        for shot in project.shots:
            gate=self.artifact_store.get('frame_gate_'+shot.shot_id)
            if not gate or not self.artifact_store.read_structured(gate)['passed']: continue
            try: await self._generate_versions(project,[shot])
            except Exception as error: self.block_shot(shot,'video_generation',str(error))

    async def _generate_versions(self,project,shots,**kwargs):
        for shot in shots:
            gate=self.artifact_store.get('frame_gate_'+shot.shot_id)
            if not gate: raise ValueError('H3 requires frame gate')
            data=self.artifact_store.read_structured(gate)
            bound=self.artifact_store.get('frame_bindings_'+shot.shot_id)
            if not data['passed'] or not bound: raise ValueError('H3 frame gate has not passed')
            refs=self.artifact_store.read_structured(bound)
            if [(r['artifact_id'],r['version'],r['sha256']) for r in refs] != [
                (r['artifact_id'],r['version'],r['sha256']) for r in data['frames']]:
                raise ValueError('H3 frame inputs differ from reviewed versions')
        await self.prepare_video_prompts(project,shots,kwargs.get('repair_contexts'))
        return await super()._generate_versions(project,shots,**kwargs)

    async def prepare_video_prompts(self,project,shots,repair_contexts=None):
        from movie_agent.quality.visual_prompts import english_visual_prompt
        capabilities=await self.media_runtime.capabilities()
        for shot in shots:
            refs=self._media_references(project,shot,repair_context=(repair_contexts or {}).get(shot.shot_id))
            strategy=self.strategy_planner.plan(shot,capabilities,refs)
            original=GenericVideoPromptCompiler().compile(shot,strategy,refs,
                repair_context=(repair_contexts or {}).get(shot.shot_id),has_authoritative_dialogue=True)
            self.video_prompt_compiler.english[original.positive_prompt]=await english_visual_prompt(self,project,original.positive_prompt)

    def existing_videos(self,project):
        return [(s,self.artifact_store.list_versions('video_'+s.shot_id)[-1]) for s in project.shots
                if self.artifact_store.get('video_'+s.shot_id)]

    def _reusable_video_generation(self,shot_id):
        reusable=super()._reusable_video_generation(shot_id)
        if reusable and self.artifact_store.get('reference_invalidation_'+shot_id):
            from movie_agent.media.video_conditioning import exact
            trace=reusable[1].provenance.parameters.get('reference_conditioning') or {}
            gate=self.artifact_store.read_structured(self.artifact_store.get('frame_gate_'+shot_id))
            if trace.get('reference_fingerprint')!=gate.get('reference_fingerprint'):return None
            if exact([MediaReference.model_validate(r) for r in trace.get('boundary_frames',[])])!=exact(
                [MediaReference.model_validate(r) for r in gate.get('frames',[])]):return None
        return reusable

    async def _technical_qc(self,project):
        for shot,video in self.existing_videos(project): self._record_evaluation(project,self.technical_qc.evaluate(shot,video))

    async def _visual_semantic_critic(self,project):
        for shot,video in self.existing_videos(project):
            try: self._record_evaluation(project,await self._vision_evaluation(project,shot,video))
            except Exception as error: self.block_shot(shot,'video_inspection',str(error))

    async def _cinematic_critic(self,project):
        for shot,video in self.existing_videos(project):
            try:
                self._record_evaluation(project,await evaluate_cinematic(self,project,shot=shot,artifact=video,
                    minimum=self.thresholds.cinematic_minimum))
            except Exception as error: self.block_shot(shot,'cinematic_critic',str(error))

    def reports(self,project):
        from movie_agent.quality.reports import QualityDimension,EvidenceStatus
        from movie_agent.services.audio_production import cues
        cinematic=[self.artifact_store.read_structured(a) for a in self.artifact_store.list_all()
                   if a.metadata.get('purpose')=='cinematic_evaluation']
        reports=[]
        for shot,video in self.existing_videos(project):
            pin=self.pin(video)
            observed=video.model_copy(update={'metadata':{**video.metadata,'sha256':pin.sha256}})
            facts={}
            for record in cinematic:
                e=record['evaluation'];proposal=record['proposal']
                if e['target_artifact_id']==video.artifact_id and e['target_artifact_version']==video.version:
                    facts={k:QualityDimension(score=v,evidence_ids=[e['evaluation_id'],*proposal['evidence_ids']],
                        status=EvidenceStatus.UNKNOWN if v is None else EvidenceStatus.PASS if v>=self.thresholds.cinematic_minimum else EvidenceStatus.FAIL)
                        for k,v in proposal.get('dimensions',{}).items()}
            audio={}
            speech=[self.artifact_store.get('speech_'+c.cue_id) for c in cues(self) if c.shot_id==shot.shot_id]
            if speech and all(a and a.metadata.get('mock') is False for a in speech):
                audio['dialogue_present']=QualityDimension(status=EvidenceStatus.PASS,evidence_ids=[a.artifact_id for a in speech])
            report=aggregate_shot(shot,observed,self.evaluations,self.media_runtime.inspections,thresholds=self.thresholds,
                cinematic_evidence=facts,audio_evidence=audio)
            invalidated=self.artifact_store.get('reference_invalidation_'+shot.shot_id)
            if invalidated:
                expected=self.artifact_store.read_structured(invalidated)['reference_fingerprint']
                trace=video.provenance.parameters.get('reference_conditioning') or {}
                if trace.get('reference_fingerprint')!=expected:
                    report=report.model_copy(update={'status':ShotQualityStatus.HUMAN_REVIEW,
                        'blocking_issue_ids':[*report.blocking_issue_ids,invalidated.artifact_id]})
            self.commit_quality('quality_'+shot.shot_id,report.model_dump(mode='json'),'shot_quality_report')
            reports.append(report)
        film=aggregate_film(project.project_id,reports,self.thresholds)
        self.commit_quality('film_quality_report',film.model_dump(mode='json'),'film_quality_report')
        return reports

    async def _repair_accept(self,project,*,shots=None):
        from movie_agent.media import MediaRepairActionType as A
        from movie_agent.quality.vision import BLOCKING
        from movie_agent.quality.budget import RepairCostPolicy,WorkKind
        self.reports(project)  # Preserve the rejected version before any repair creates its successor.
        # Frame gate history remains unchanged when temporal video drift is discovered.
        for shot,video in self.existing_videos(project):
            if shots is not None and shot.shot_id not in {s.shot_id for s in shots}:continue
            inspection=next((i for i in reversed(self.media_runtime.inspections) if i.target_artifact_id==video.artifact_id
                             and i.target_artifact_version==video.version),None)
            if inspection is None or inspection.decision==VisionDecision.PASS: continue
            if video.version>=2 or inspection.decision==VisionDecision.HUMAN_REVIEW:
                self.block_shot(shot,'video_quality','Automatic video budget or uncertain diagnosis requires human review')
                continue
            costs=RepairCostPolicy()
            self.commit_quality('repair_cost_'+shot.shot_id+'_v'+str(video.version),{
                'issues':[{'issue_id':i.issue_id,'estimate':costs.route(i.issue_type.value,kontext_available=True).model_dump(mode='json')
                    if costs.route(i.issue_type.value,kontext_available=True) else None} for i in inspection.issues if i.severity in BLOCKING],
                'chosen':costs.estimate(WorkKind.VIDEO).model_dump(mode='json'),
                'reason':'Source frames already passed exact identity gate; temporal video repair preserves those anchors.',
                'video_repair_limit':1},'targeted_repair_cost')
            plan=self.repair_planner.plan_media(inspection,retry_budget=1,retry_count=video.version-1,
                supported_actions={A.REGENERATE_VIDEO,A.REWRITE_PROMPT,A.CHANGE_CAMERA_CONTROL})
            self.commit_quality(plan.repair_plan_id,plan.model_dump(mode='json'),'media_repair_plan')
            if plan.requires_human: self.block_shot(shot,'targeted_repair','Unsupported automatic repair; review required'); continue
            try:
                output=(await self._generate_versions(project,[shot],repair_plan_ids={shot.shot_id:plan.repair_plan_id},
                    repair_contexts={shot.shot_id:plan.repair_context},target_versions={shot.shot_id:video.version+1}))[0]
                self._record_evaluation(project,self.technical_qc.evaluate(shot,output))
                self._record_evaluation(project,await self._vision_evaluation(project,shot,output))
                self._record_evaluation(project,await evaluate_cinematic(self,project,shot=shot,artifact=output,
                    minimum=self.thresholds.cinematic_minimum))
            except Exception as error: self.block_shot(shot,'video_repair',str(error))
        reports=self.reports(project)
        for report in reports:
            if report.status==ShotQualityStatus.ACCEPTED:
                self._select(project,report.artifact_id,report.artifact_version)
        accepted=[r for r in reports if r.status==ShotQualityStatus.ACCEPTED]
        if not accepted: return True  # audio DAG can still progress; post will fail closed
        clips=[]; offset=0
        for report in accepted:
            clips.append(TimelineClip(clip_id='p5_'+report.shot_id,shot_id=report.shot_id,
                artifact_id=report.artifact_id,version=report.artifact_version,sha256=report.sha256,
                start_time_seconds=offset,duration_seconds=report.duration_seconds))
            offset+=report.duration_seconds
        # Post's existing migration attaches exact native tracks, then authoritative TTS/music.
        self.timeline=Timeline(project_id=project.project_id,duration_seconds=offset,
            video_tracks=[VideoTrack(clips=[c.model_copy(update={'sha256':None}) for c in clips])])
        self._durable_media_checkpoint()
        return True

    async def _full_film_review(self,project):
        await super()._full_film_review(project)
        reports=self.reports(project)
        proxy=film_proxy(project,reports,self.timeline,self.media_runtime.inspections)
        rough=self._required_artifact('rough_cut'); proxy['rough_cut_version']=rough.version
        self.commit_quality('film_review_proxy',proxy,'film_review_proxy')
        evaluation=await evaluate_cinematic(self,project,proxy=proxy,minimum=self.thresholds.cinematic_minimum)
        self._record_evaluation(project,evaluation)
        film=self.artifact_store.read_structured(self.artifact_store.get('film_quality_report'))
        film.update(review_proxy_artifact_id='film_review_proxy',cinematic_evaluation_id=evaluation.evaluation_id)
        self.commit_quality('film_quality_report',film,'film_quality_report')

    async def _execute_node(self,node_id,project,production,*,auto_approve):
        if node_id=='audio_post':
            reports=self.reports(project)
            if not any(r.status==ShotQualityStatus.ACCEPTED for r in reports):
                self.human_gates.pending_for_node(node_id) or self.human_gates.request(
                    project.project_id,node_id,HumanGateType.AGENT_ESCALATION,
                    'No accepted visual shots remain. Review failed references and exhausted quality budgets before further production.',
                    ['reference_identity_set','film_quality_report'])
                production.set_status(node_id,WorkflowNodeStatus.WAITING_HUMAN,progress=0)
                self._durable_media_checkpoint()
                return False
        if node_id in {'story_gate','shot_gate'}:
            self.commit_quality('p5_autonomous_'+node_id,{'authorization':'P5 overnight user request',
                'policy':'AI planning permitted; no human approval asserted'},'autonomous_planning_policy')
            return True
        if node_id=='final_gate':
            approved=await super()._execute_node(node_id,project,production,auto_approve=False)
            if approved:return True
            from movie_agent.services.post_production import technical_candidate
            await technical_candidate(self,project)
            return False
        return await super()._execute_node(node_id,project,production,auto_approve=False)
