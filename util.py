import re
import discord


class InteractionAdapter:
    """
    Adapter to make Interaction objects compatible with message-expecting code.
    Provides message-like interface for slash command interactions.
    """
    def __init__(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.author = getattr(interaction, "user", getattr(interaction, "author", None))
        self.channel = getattr(interaction, "channel", None)
        self.guild = getattr(interaction, "guild", None)
        self.attachments = []  # Can be populated for file uploads
        self.content = getattr(interaction, "content", "")  # Slash commands don't have message content

    async def reply(self, content=None, **kwargs):
        """Reply via interaction response or followup"""
        # Scenario A: It's a Slash Command / Context Menu
        if isinstance(self.interaction, discord.Interaction):
            if not self.interaction.response.is_done():
                await self.interaction.response.send_message(content, **kwargs)
            else:
                await self.interaction.followup.send(content, **kwargs)
        
        # Scenario B: It's a standard Message (like in your on_message event)s
        elif isinstance(self.interaction, discord.Message):
            await self.interaction.reply(content, **kwargs)

    async def add_reaction(self, emoji):
        """An interaction can't be reacted to, so surface the acknowledgement as a
        short followup instead of silently dropping it (which would also leave a
        deferred interaction hanging)."""
        if isinstance(self.interaction, discord.Interaction):
            try:
                if self.interaction.response.is_done():
                    await self.interaction.followup.send(emoji)
                else:
                    await self.interaction.response.send_message(emoji)
            except discord.DiscordException:
                pass
        elif isinstance(self.interaction, discord.Message):
            await self.interaction.add_reaction(emoji)

    @property
    def jump_url(self):
        """Generate a pseudo jump URL for the interaction"""
        return f"https://discord.com/channels/{self.guild.id if self.guild else '@me'}/{self.channel.id}"


class ValueRetainingRegexMatcher:
    '''This is a load of BS to just get around not using PEP 572'''
    def __init__(self, match_str):
        self.match_str = match_str


    def match(self, regex):
        self.retained = re.match(regex, self.match_str)
        return bool(self.retained)


    def search(self, regex):
        self.retained = re.search(regex, self.match_str)
        return bool(self.retained)


    def group(self, i):
        return self.retained.group(i)

