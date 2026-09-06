"""Model-independent prompt policy; never rewrites canonical inputs or schemas."""
import json


class LanguagePolicy:
    @staticmethod
    def instruction(output_language: str | None) -> str:
        language = (output_language or '').strip() or 'en'
        normalized = language.lower().replace('_', '-')
        if normalized in {'zh', 'zh-cn', 'zh-hans', 'zh-hans-cn'}:
            requirement = '所有面向人类的自然语言内容必须使用简体中文。'
        elif normalized in {'en', 'en-us', 'en-gb', 'english'}:
            requirement = 'All human-readable natural-language content must be written in English.'
        else:
            requirement = ('All human-readable natural-language content must be written in '
                           f'the requested output language: {json.dumps(language, ensure_ascii=False)}.')
        return requirement + (
            '\nThis applies to story summaries, themes, acts, character arcs, action, dialogue, '
            'scene purpose, shot narrative, lighting, camera intent and frame descriptions, '
            'including every structured-output repair. '
            'Keep JSON keys, contract field names, IDs, enum literals, schema names and constants, '
            'technical literals, URLs, and verbatim user constraints unchanged. '
            'Never translate values the contract requires copying verbatim, including '
            'user_constraints, must_preserve, world_rules and existing canonical state values.'
        )
