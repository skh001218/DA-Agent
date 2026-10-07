"""Discord forum publishing with durable progress and marker-based recovery."""
import asyncio
import json
from io import BytesIO

from .discord_results import card_marker, legacy_card_marker
from .discord_pdf import pdf_filename, render_submission_pdf
from .errors import DomainError


# The forum endpoint rejected a valid character count with HTTP 413 for a
# Korean payload. Keep a conservative serialized UTF-8 budget as well; the PDF
# retains the full submission and assessment.
MAX_EMBED_BYTES = 6000


def embed_bytes(embeds):
    return len(json.dumps([e.to_dict() for e in embeds], ensure_ascii=False,
                          separators=(',', ':')).encode('utf-8'))


def clip_bytes(text, limit):
    if len(text.encode('utf-8')) <= limit:
        return text
    return text.encode('utf-8')[:max(0, limit - 3)].decode('utf-8', errors='ignore') + '…'


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

    def post_embeds(self, submission, user):
        """One compact starter card; the existing PDF retains every section."""
        import discord
        from .discord_result_summary import legacy_summary, render_summary

        summary = submission.get('forum_summary') or legacy_summary(submission)
        title, description = render_summary(summary)
        embed = discord.Embed(title=title, description=description,
            colour=0xe3a23b if summary.get('held') or summary.get('warning') else 0x315d91)
        embed.set_author(name=user.display_name[:80])
        embed.set_footer(text=card_marker(submission, 0))
        source = submission['cards'][0].get('source_url')
        if source:
            embed.add_field(name='원래 과제 공간', value='[과제 스레드 열기](' + source + ')', inline=False)
        if len(embed) > 6000 or embed_bytes([embed]) > MAX_EMBED_BYTES:
            raise DomainError('result_embed_size', '결과 요약이 게시 한도를 초과했습니다. 평가 기록은 보존했습니다. /resume으로 다시 시도하세요.')
        return [embed]

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
        if not all(getattr(bot, name, False) for name in ('view_channel', 'send_messages', 'send_messages_in_threads', 'embed_links', 'read_message_history', 'attach_files')):
            raise DomainError('result_post_permission', 'DA-Result에서 봇의 채널 보기·게시글 만들기·스레드 발언·링크 삽입·기록 읽기·파일 첨부 권한을 확인한 뒤 /submit하세요.')
        owner = forum.permissions_for(user)
        if not owner.view_channel or not owner.read_message_history:
            raise DomainError('result_owner_permission', '본인이 DA-Result 결과를 읽을 수 없습니다. 운영자가 채널 접근 권한을 확인한 뒤 /submit하세요.')
        return forum

    async def find_post(self, forum, submission):
        """A matching title alone is insufficient: verify bot and full immutable IDs."""
        import discord
        candidates = [t for t in await forum.guild.active_threads() if t.parent_id == forum.id]
        candidates.extend([t async for t in forum.archived_threads(limit=None)])
        for thread in candidates:
            if thread.name != submission['post_name']:
                continue
            try:
                starter = await thread.fetch_message(thread.id)
            except discord.NotFound:
                # A rejected forum create can leave an empty thread. Never bind
                # it by title alone. Uncertain creation still cannot create a
                # replacement in publish(), even if this lookup finds nothing.
                continue
            if starter.author.id == self.client.user.id and any(e.footer.text in {card_marker(submission, 0), legacy_card_marker(submission, 0)} for e in starter.embeds):
                return thread
        return None

    async def publish(self, parent, user, submission, publication, save):
        import discord
        from .discord_pdf_view import ResultPDFView
        forum = await self.forum(parent, user)
        filename = pdf_filename(submission)
        embeds = self.post_embeds(submission, user)

        async def pdf_file():
            try:
                data = await asyncio.to_thread(render_submission_pdf, submission)
            except DomainError:
                raise
            except Exception as exc:
                raise DomainError('result_pdf_generation', 'PDF 생성에 실패했습니다. 평가 기록은 보존했습니다. /resume으로 다시 시도하세요.') from exc
            if len(data) > forum.guild.filesize_limit:
                raise DomainError('result_pdf_size', 'PDF가 서버 파일 첨부 한도를 초과했습니다. 운영자가 업로드 한도를 확인한 뒤 /resume하세요.')
            return discord.File(BytesIO(data), filename=filename)
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
                file = await pdf_file()
                await save(status='creating', forum_id=str(forum.id))
                try:
                    created = await forum.create_thread(name=submission['post_name'], embeds=embeds,
                        file=file, view=ResultPDFView([forum.guild.id]),
                        allowed_mentions=discord.AllowedMentions.none(), applied_tags=tags,
                        reason='저장된 최종 제출·평가 결과 게시')
                    thread = created.thread
                except discord.HTTPException as exc:
                    await save(status='failed' if 400 <= exc.status < 500 else 'uncertain')
                    raise
                finally:
                    file.close()
        await save(status='partial', forum_id=str(forum.id), post_id=str(thread.id))
        if thread.archived:
            await thread.edit(archived=False)
        starter = await thread.fetch_message(thread.id)
        if (starter.author.id != self.client.user.id
                or not any(e.footer.text in {card_marker(submission, 0), legacy_card_marker(submission, 0)}
                           for e in starter.embeds)):
            raise DomainError('result_post_author', '결과 게시글 작성자와 제출 연결을 확인하세요.')
        if not any(a.filename == filename for a in starter.attachments):
            file = await pdf_file()
            try:
                await starter.edit(embeds=embeds, attachments=[*starter.attachments, file],
                                   view=ResultPDFView([forum.guild.id]),
                                   allowed_mentions=discord.AllowedMentions.none())
            finally:
                file.close()
        else:
            await starter.edit(embeds=embeds, view=ResultPDFView([forum.guild.id]),
                               allowed_mentions=discord.AllowedMentions.none())
        await save(status='published', forum_id=str(forum.id), post_id=str(thread.id), error_code=None)
        return f"https://discord.com/channels/{forum.guild.id}/{thread.id}"
