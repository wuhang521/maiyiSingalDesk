"""Explicit real-provider smoke using synthetic material and server-local key only."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

server = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(server))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', required=True, type=Path)
    parser.add_argument('--hermes-env', default='/home/hermes/.hermes/.env', type=Path)
    args = parser.parse_args()
    from dotenv import dotenv_values
    values = dotenv_values(args.hermes_env)
    key = values.get('DASHSCOPE_API_KEY')
    if not isinstance(key, str) or not key.strip() or key.startswith('${'):
        raise SystemExit('Existing server-local DashScope credential is unavailable; no request sent.')
    os.environ['TASK_DATABASE_URL'] = 'sqlite://'
    os.environ['TASK_KNOWLEDGE_QWEN_API_KEY'] = key
    os.environ['TASK_KNOWLEDGE_CLOUD_ASR_PROVIDER'] = 'qwen'
    os.environ['TASK_KNOWLEDGE_CLOUD_ASR_ENABLED'] = 'true'
    os.environ['TASK_KNOWLEDGE_EMBEDDING_PROVIDER'] = 'qwen'
    os.environ['TASK_KNOWLEDGE_EMBEDDING_MODEL'] = 'text-embedding-v4'
    os.environ['TASK_KNOWLEDGE_SEMANTIC_ENABLED'] = 'true'
    from app import knowledge_asr, knowledge_semantic
    speech = knowledge_asr.recognize(args.audio, args.audio.name, 'synthetic-live-smoke')
    words = ['knowledge', 'base', 'integration', 'test', 'verify', 'source']
    matches = sum(word in speech.text.lower() for word in words)
    vectors, model = await knowledge_semantic.embed([
        '如何保存资料来源并核对引用？', '保存资料时应先记录原始来源，核对正文，再引用。', '周末准备去公园散步，记得带雨伞。'])
    scores = [sum(a * b for a, b in zip(vectors[0], row)) for row in vectors[1:]]
    result = dict(provider='qwen', asr_model='qwen3-asr-flash', asr_method=speech.method,
                  asr_keyword_matches=matches, expected_keywords=len(words), transcript=speech.text,
                  locator=speech.chunks[0][0], embedding_model=model, embedding_dimensions=len(vectors[0]),
                  relevant_score=scores[0], unrelated_score=scores[1],
                  passed=matches >= 5 and scores[0] > scores[1],
                  scope='Synthetic audio and three synthetic texts; no private library, production write or notification.')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['passed']: raise SystemExit(1)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        # Adapter errors are sanitized; never include provider bodies or env.
        print(json.dumps(dict(passed=False, error_type=type(error).__name__, detail=str(error) if type(error).__name__ in {'AsrError', 'HTTPException'} else 'Provider smoke did not finish'), ensure_ascii=False))
        raise SystemExit(1)
