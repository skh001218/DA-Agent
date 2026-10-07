"""Render an explicitly edited, three-page learning template without model calls.

This local preview does not change Discord publication or rewrite saved reports.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def render_preview(data):
    from da_agent.discord_pdf_summary import render_learning_pdf
    return render_learning_pdf(data, preview=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('template/discord-result-summary.example.json'))
    parser.add_argument('--output', type=Path, default=Path('output/pdf/concise-result-preview.pdf'))
    args = parser.parse_args()
    data = json.loads(args.data.read_text(encoding='utf-8'))
    result = render_preview(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result)
    print(f'Created 3-page PDF: {args.output.resolve()} ({len(result)} bytes)')


if __name__ == '__main__':
    main()
