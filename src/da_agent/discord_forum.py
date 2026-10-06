"""Discord forum publishing with durable progress and marker-based recovery."""
from .discord_results import card_marker, legacy_card_marker
from .errors import DomainError


class ResultForumPublisher:
    def __init__(self, client):
        self.client = client

    def embed(self, submission, index, user):
        import discord
        card = submission['cards'][index]
        embed = discord.Embed(title=card['title'][:256], description=card['description'],
                              colour=0xe3a23b if card.get('held') else 0x315d91)
        embed.set_author(name=user.display_name[:256])
        embed.set_footer(text=card_marker(submission, index))
        if card.get('source_url'):
            embed.add_field(name='원래 과제 공간', value='[과제 스레드 열기](' + card['source_url'] + ')', inline=False)
        return embed

    async def forum(self, parent, user):
        import discord
        guild = parent.guild
        channels = await guild.fetch_channels()
        forums = sorted((c for c in channels if isinstance(c, discord.ForumChannel) and c.name.casefold() == 'da-result'), key=lambda c: c.id)
        if forums:
            forum = forums[0]
        else:
            if any(c.name.casefold() == 'da-result' for c in channels):
                raise DomainError('result_channel_type', 'DA-Result라는 이름의 채널이 있지만 포럼이 아닙니다. 운영자가 포럼 채널을 준비한 뒤 /submit을 다시 실행하세요.')
            if not parent.permissions_for(guild.me).manage_channels:
                raise DomainError('result_create_permission', 'DA-Result 포럼 생성에 봇의 채널 관리 권한이 필요합니다. 운영자가 포럼을 만들거나 권한을 설정한 뒤 /submit을 다시 실행하세요.')
            forum = await guild.create_forum('DA-Result', category=parent.category, overwrites=parent.overwrites,
                topic='분석 훈련 제출 보고서와 평가 결과. 게시글에서 결과에 대한 의견을 이어가세요.',
                default_layout=discord.ForumLayoutType.list_view,
                reason='사용자 요청: 최종 제출 결과를 정리할 DA-Result 포럼')
        bot = forum.permissions_for(guild.me)
        if not all(getattr(bot, name, False) for name in ('view_channel', 'send_messages', 'send_messages_in_threads', 'embed_links', 'read_message_history')):
            raise DomainError('result_post_permission', 'DA-Result에서 봇의 채널 보기·게시글 만들기·스레드 발언·링크 삽입·기록 읽기 권한을 확인한 뒤 /submit하세요.')
        owner = forum.permissions_for(user)
        if not owner.view_channel or not owner.read_message_history:
            raise DomainError('result_owner_permission', '본인이 DA-Result 결과를 읽을 수 없습니다. 운영자가 채널 접근 권한을 확인한 뒤 /submit하세요.')
        return forum

    async def find_post(self, forum, submission):
        """A matching title alone is insufficient: verify bot and full immutable IDs."""
        candidates = [t for t in await forum.guild.active_threads() if t.parent_id == forum.id]
        candidates.extend([t async for t in forum.archived_threads(limit=None)])
        for thread in candidates:
            if thread.name != submission['post_name']:
                continue
            starter = await thread.fetch_message(thread.id)
            if starter.author.id == self.client.user.id and any(e.footer.text in {card_marker(submission, 0), legacy_card_marker(submission, 0)} for e in starter.embeds):
                return thread
        return None

    async def publish(self, parent, user, submission, publication, save):
        import discord
        forum = await self.forum(parent, user)
        if publication.get('post_id'):
            try:
                thread = await self.client.fetch_channel(int(publication['post_id']))
            except discord.NotFound:
                raise DomainError('result_post_missing', '저장된 결과 게시글이 삭제됐습니다. 평가 기록은 보존돼 있습니다. 운영자에게 게시 연결 복구를 요청하세요.') from None
            if not isinstance(thread, discord.Thread) or thread.parent_id != forum.id:
                raise DomainError('result_post_parent', '저장된 결과 게시글의 포럼 연결을 확인하세요.')
        else:
            thread = await self.find_post(forum, submission)
            if thread is None:
                if publication.get('status') in {'creating', 'uncertain'}:
                    raise DomainError('result_publish_uncertain', '이전 게시글 생성 결과를 확인할 수 없습니다. 중복 게시를 막기 위해 새 글을 만들지 않았습니다. 운영자가 DA-Result 연결을 확인해주세요.')
                tags = []
                if forum.flags.require_tag:
                    tags = [tag for tag in forum.available_tags if not tag.moderated or forum.permissions_for(forum.guild.me).manage_threads][:1]
                    if not tags:
                        raise DomainError('result_tag_required', 'DA-Result는 태그가 필수입니다. 봇이 적용할 수 있는 태그를 준비한 뒤 /submit하세요.')
                await save(status='creating', forum_id=str(forum.id))
                try:
                    created = await forum.create_thread(name=submission['post_name'], embed=self.embed(submission, 0, user),
                        allowed_mentions=discord.AllowedMentions.none(), applied_tags=tags,
                        reason='저장된 최종 제출·평가 결과 게시')
                    thread = created.thread
                except discord.HTTPException as exc:
                    await save(status='failed' if 400 <= exc.status < 500 else 'uncertain')
                    raise
        await save(status='partial', forum_id=str(forum.id), post_id=str(thread.id))
        if thread.archived:
            await thread.edit(archived=False)
        present = set()
        async for message in thread.history(limit=None):
            if message.author.id == self.client.user.id:
                present.update(e.footer.text for e in message.embeds)
                for index in range(len(submission['cards'])):
                    if any(e.footer.text == legacy_card_marker(submission, index) or
                           (e.footer.text == card_marker(submission, index) and index == 0)
                           for e in message.embeds):
                        await message.edit(embed=self.embed(submission, index, user), allowed_mentions=discord.AllowedMentions.none())
                        present.add(card_marker(submission, index))
        for index in range(len(submission['cards'])):
            if card_marker(submission, index) not in present:
                await thread.send(embed=self.embed(submission, index, user), allowed_mentions=discord.AllowedMentions.none())
        await save(status='published', forum_id=str(forum.id), post_id=str(thread.id), error_code=None)
        return f"https://discord.com/channels/{forum.guild.id}/{thread.id}"
