"""Buffer OpenAI SSE final content; no partial JSON crosses the provider boundary."""
import asyncio
import json
import httpx


async def collect_completion(response: httpx.Response, *, inactivity: float, max_chars: int) -> dict:
    content: list[str] = []
    size = 0
    finish = None
    remote_id = None
    usage = {}
    lines = response.aiter_lines().__aiter__()
    while True:
        try:
            async with asyncio.timeout(inactivity):
                line = await anext(lines)
        except TimeoutError as exc:
            raise httpx.ReadTimeout('Stream inactivity timeout') from exc
        except StopAsyncIteration as exc:
            raise httpx.ReadError('Stream ended without completion marker') from exc
        if not line or line.startswith(':'):
            continue
        if not line.startswith('data:'):
            raise httpx.ReadError('Invalid completion stream framing')
        payload = line[5:].strip()
        if payload == '[DONE]':
            if finish is None:
                raise httpx.ReadError('Stream ended without finish reason')
            return {'id': remote_id, 'usage': usage,
                    'choices': [{'message': {'content': ''.join(content)}, 'finish_reason': finish}]}
        try:
            chunk = json.loads(payload)
            if 'error' in chunk:
                raise ValueError('Stream error')
            remote_id = chunk.get('id') or remote_id
            if isinstance(chunk.get('usage'), dict):
                usage = chunk['usage']
            for choice in chunk.get('choices', []):
                if choice.get('index', 0) != 0:
                    raise ValueError('Unexpected choice')
                delta = choice.get('delta', {}).get('content')
                if delta is not None:
                    if not isinstance(delta, str):
                        raise ValueError('Invalid delta')
                    size += len(delta)
                    if size > max_chars:
                        raise ValueError('Stream exceeds bounded content buffer')
                    content.append(delta)
                finish = choice.get('finish_reason') or finish
        except (ValueError, TypeError, AttributeError) as exc:
            raise httpx.ReadError('Invalid completion stream') from exc
