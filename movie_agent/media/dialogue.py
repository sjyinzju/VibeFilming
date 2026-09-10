"""Deterministic canonical dialogue extraction and shot-relative placement."""
import re
from movie_agent.media.contracts import DialogueCue


class DialogueTimingReview(ValueError):
    """Requires an explicit screenplay/timing decision, never truncated speech."""


def estimated_seconds(text, language):
    chinese = len(re.findall(r'[\u3400-\u9fff]', text))
    words = len(re.findall(r'[A-Za-z0-9]+', text))
    return max(.8, chinese / 3.8 + words / 2.5 + .2)


def extract_dialogue(project, profiles, settings):
    characters = {c.character_id: c for c in project.characters}
    names = {}
    for character in characters.values():
        for label in (character.name, character.character_id):
            names.setdefault(label.strip().casefold(), set()).add(character.character_id)
    scripts = {s.scene_id: s for s in project.screenplay.scenes} if project.screenplay else {}
    output = []
    for shot in project.shots:
        script = scripts.get(shot.scene_id)
        pairs = []
        for raw in shot.narrative.dialogue:
            line = raw.strip()
            prefix = re.match(r'^([^:：]+)[:：]\s*(.+)$', line, re.S)
            if prefix:
                matches = names.get(prefix[1].strip().casefold(), set())
                if len(matches) != 1:
                    raise DialogueTimingReview(f'Ambiguous canonical speaker in {shot.shot_id}')
                character, text = next(iter(matches)), prefix[2].strip()
            else:
                text = line
                matches = {p.character_id for p in shot.performances if p.dialogue and p.dialogue.strip() == text}
                matches.update(d.character_id for d in script.dialogue if d.text.strip() == text) if script else None
                if not matches and len({p.character_id for p in shot.performances}) == 1:
                    matches = {shot.performances[0].character_id}
                if len(matches) != 1:
                    raise DialogueTimingReview(f'Ambiguous canonical speaker in {shot.shot_id}')
                character = next(iter(matches))
            if character not in characters or not text:
                raise DialogueTimingReview('Dialogue speaker/text is not canonical')
            pairs.append((character, text))
        if not pairs:
            pairs = [(p.character_id, p.dialogue.strip()) for p in shot.performances if p.dialogue and p.dialogue.strip()]
        if not pairs and script and script.dialogue:
            if sum(s.scene_id == shot.scene_id for s in project.shots) == 1:
                pairs = [(d.character_id, d.text) for d in script.dialogue]
        if not pairs:
            continue
        # Committed screenplay uses leading parentheses for performance direction.
        # Keep that direction as prosody intent; it is not a spoken word or subtitle.
        spoken, directions = [], []
        for character, text in pairs:
            intent = []
            while match := re.match(r'^\s*(?:（([^（）]+)）|\(([^()]+)\))\s*', text):
                intent.append(match[1] or match[2])
                text = text[match.end():]
            spoken.append((character, text))
            directions.append(intent)
        pairs = spoken
        if any(character not in characters or not text.strip() for character, text in pairs):
            raise DialogueTimingReview('Dialogue speaker/text is not canonical')
        pre, post, gap = settings.dialogue_pre_roll_seconds, settings.dialogue_post_roll_seconds, settings.dialogue_minimum_gap_seconds
        available = shot.duration_seconds - pre - post - gap*(len(pairs)-1)
        estimates = [estimated_seconds(text, project.brief.output_language) for _, text in pairs]
        if available <= 0 or sum(estimates) > available * 1.1:
            raise DialogueTimingReview(f'Dialogue cannot fit shot {shot.shot_id}; extend timing or revise canonical text')
        cursor = pre
        for i, ((character, text), estimate) in enumerate(zip(pairs, estimates)):
            profile = profiles.get(character)
            slot = available * estimate / sum(estimates)
            performance = next((p for p in shot.performances if p.character_id == character), None)
            output.append(DialogueCue(cue_id=f'dialogue_{shot.shot_id}_{i+1}',
                project_id=project.project_id, scene_id=shot.scene_id, shot_id=shot.shot_id,
                character_id=character, text=text, voice_profile_id=profile.voice_profile_id if profile else f'voice_profile_{character}',
                voice_profile_version=profile.version if profile else 1,
                target_start_seconds=cursor, target_end_seconds=cursor+slot,
                emotion=performance.emotion_start or '' if performance else '',
                prosody=directions[i],
                language=project.brief.output_language))
            cursor += slot + gap
    # A silent cutaway in a multi-shot scene is valid. Verify scene-wide assignment
    # after extraction instead of trying to put every screenplay line on every shot.
    def spoken_text(text):
        while match := re.match(r'^\s*(?:（([^（）]+)）|\(([^()]+)\))\s*', text):
            text=text[match.end():]
        return text.strip()
    assigned={(c.scene_id,c.character_id,c.text) for c in output}
    for scene_id,script in scripts.items():
        if sum(s.scene_id==scene_id for s in project.shots)<=1: continue
        if any((scene_id,d.character_id,spoken_text(d.text)) not in assigned for d in script.dialogue):
            raise DialogueTimingReview('Screenplay dialogue needs explicit shot assignment')
    return output


def check_duration(cue, duration):
    if duration <= 0 or duration > cue.target_end_seconds-cue.target_start_seconds + .02:
        raise DialogueTimingReview(f'Measured speech does not fit {cue.cue_id}; timing repair required')
    return cue.target_start_seconds, cue.target_start_seconds+duration
