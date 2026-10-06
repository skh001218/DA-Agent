"""Read mounted secrets and start the isolated Discord Gateway process."""
import os
from pathlib import Path
from da_agent import discord_bot

os.environ['DISCORD_BOT_TOKEN'] = Path('/run/secrets/discord_token').read_text(encoding='utf-8-sig').strip()
original_create_client = discord_bot.create_client

def create_client(service, settings):
    client = original_create_client(service, settings)
    @client.event
    async def on_ready():
        print(f'Discord ready: bot_id={client.user.id}; guilds={list(settings.guild_ids)}', flush=True)
    return client

discord_bot.create_client = create_client
discord_bot.main()
