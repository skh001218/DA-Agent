"""Generate a Korean multi-page QA sample without Discord credentials."""
from pathlib import Path

from da_agent.discord_pdf import render_submission_pdf
from da_agent.discord_transport import safe_chunks


def main():
    report = ('튜토리얼 완료율 분석: 분모는 가입자, 분자는 완료한 사용자입니다. '
              '중복 이벤트를 제외하고 채널별 차이를 확인합니다. <태그> & SQL user_id\n') * 120
    cards = [dict(title='제출 결과 요약', description='분석 훈련 · 보고 버전 1\n평가 점수: 75/100 · 사람 검토 대기.',
                  source_url='https://discord.com/channels/example/source')]
    cards.extend(dict(title=f'제출 보고서 · {i}', description=chunk)
                 for i, chunk in enumerate(safe_chunks(report), 1))
    cards.append(dict(title='평가 근거와 다음 개선 행동', description='관측만으로 원인을 확정하지 않습니다.\n후속 검증: 유입 채널과 기간을 맞춰 비교합니다.\n마지막 줄: 한글·숫자 123·기호 < > & _ \\'))
    submission = dict(session_id='qa-session', evaluation_id='qa-evaluation',
                      post_name='분석 훈련 · PDF 화면 검증', cards=cards)
    output = Path('tests/artifacts/discord-pdf-2026-10-06')
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'analysis-qa.pdf'
    path.write_bytes(render_submission_pdf(submission))
    print(path.resolve())


if __name__ == '__main__':
    main()
