"""Restart-safe download button; fresh attachment fetched on every click."""
import discord


class ResultPDFView(discord.ui.View):
    def __init__(self, guild_ids):
        super().__init__(timeout=None)
        self.guild_ids = {str(g) for g in guild_ids}

    @discord.ui.button(label='PDF 다운로드', style=discord.ButtonStyle.primary,
                       custom_id='da-result:pdf:v1')
    async def download(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = interaction.channel
            if (not interaction.guild or str(interaction.guild.id) not in self.guild_ids
                    or not isinstance(channel, discord.Thread)
                    or not isinstance(channel.parent, discord.ForumChannel)):
                await interaction.followup.send('허용된 결과 포럼에서 다운로드하세요.', ephemeral=True)
                return
            permissions = channel.permissions_for(interaction.user)
            if not permissions.view_channel or not permissions.read_message_history:
                await interaction.followup.send('결과 게시글 접근 권한을 확인하세요.', ephemeral=True)
                return
            message = await channel.fetch_message(interaction.message.id)
            if message.author.id != interaction.client.user.id:
                await interaction.followup.send('봇이 게시한 결과에서 다운로드하세요.', ephemeral=True)
                return
            attachment = next((a for a in message.attachments
                               if a.filename.startswith('analysis-') and a.filename.endswith('.pdf')), None)
            if attachment is None:
                await interaction.followup.send('PDF 첨부가 없습니다. 과제에서 /resume으로 복구하세요.', ephemeral=True)
                return
            file = await attachment.to_file()
            try:
                await interaction.followup.send(file=file, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
            finally:
                file.close()
        except discord.HTTPException:
            await interaction.followup.send('PDF를 가져오지 못했습니다. 게시글과 첨부 접근 권한을 확인하고 다시 시도하세요.', ephemeral=True)
