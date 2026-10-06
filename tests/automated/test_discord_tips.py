import asyncio

from da_agent.discord_presentation import COMMAND_TIPS, command_tip
from test_discord_transport import setup


def test_tip_works_without_training_or_model_and_leaves_state_untouched():
    service, gateway, transport, event = setup()
    service.session = None
    asyncio.run(transport.command(event, 'tip', text='/report'))
    assert service.calls == []
    assert gateway.sent == []
    assert gateway.events == ['defer']
    assert 'append' in gateway.replies[0]
    assert 'True' in gateway.replies[0] and 'False' in gateway.replies[0]


def test_tip_slash_normalization_list_unknown_and_coverage():
    assert command_tip(' /REPORT ') == command_tip('report')
    assert '알 수 없는 명령어' in command_tip('missing')
    for name in COMMAND_TIPS:
        assert '/' + name in command_tip()
        assert '사용 예시' in command_tip(name)


def test_tip_obeys_server_allowlist():
    service, gateway, transport, event = setup(guild=99)
    asyncio.run(transport.command(event, 'tip', text='report'))
    assert service.calls == []
    assert '허용된 서버' in gateway.replies[0]
