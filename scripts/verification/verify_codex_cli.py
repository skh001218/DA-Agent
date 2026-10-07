"""Verify subscription-only CLI transport, without logging prompts or raw failures."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from da_agent.codex_provider import CodexCliProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', default='codex')
    parser.add_argument('--home')
    parser.add_argument('--model')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--live', action='store_true', help='Consumes subscription usage for one JSON probe')
    args = parser.parse_args()
    provider = CodexCliProvider(executable=args.executable, home=args.home,
                                model=args.model, timeout=args.timeout)
    result = provider.status()
    if args.live and result.get('connected'):
        result = provider.review([
            {'role': 'developer', 'content': 'Return a JSON object matching the supplied schema. Set ok to true.'},
            {'role': 'user', 'content': json.dumps({'schema': {
                'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                'required': ['ok'], 'additionalProperties': False}})},
        ])
        if result['state'] == 'completed' and json.loads(result['text']) != {'ok': True}:
            result = {'state': 'error', 'reason': 'probe_contract_invalid'}
    print(json.dumps({k: v for k, v in result.items() if k != 'text'}, ensure_ascii=False))
    return 0 if result['state'] in {'ready', 'completed'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
