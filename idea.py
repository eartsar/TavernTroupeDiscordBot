import discord
import github



class IdeaManager():
    def __init__(self, bot, github_token, maintainer_id):
        self.bot = bot
        self.github_token = github_token
        self.maintainer_id = maintainer_id
        self.pending_cache = {}


    async def reaction_handler(self, user, reaction):
        '''
        Hook that the main bot application calls on a reaction to see
        if this object should do anything about it
        '''
        # Ignore reactions to messages that are not disclaimer messages
        if reaction.message not in self.pending_cache:
            return
        
        # If the reaction is from the owner, and a valid option, interpet it. Otherwise, purge.
        if user.id == self.maintainer_id and reaction.count > 1:
            if reaction.emoji == '🆗':
                client = github.Github(self.github_token)
                repo = client.get_repo("eartsar/TavernTroupeDiscordBot")
                issue = repo.create_issue(
                    title=self.pending_cache[reaction.message]['title'],
                    body=self.pending_cache[reaction.message]['body'],
                    assignee="eartsar",
                    labels=[repo.get_label("idea")]
                )

                await reaction.message.reply('Idea accepted.')
                return await self.pending_cache[reaction.message]['request'].reply('Your idea was accepted by the maintainer.')

            elif reaction.emoji == '🚫':
                await reaction.message.reply('Idea rejected.')
                await self.pending_cache[reaction.message]['request'].reply('Your idea was rejected by the maintainer.')
            
            del self.pending_cache[reaction.message]
        else:
            await reaction.remove(user)


    async def submit(self, message, title):
        await message.reply('Your idea has been submitted to the maintainer for consideration!')

        maintainer = self.bot.get_user(self.maintainer_id)
        msg = await maintainer.send(f'Idea Request: {message.jump_url}\n>>> {title}\n(suggested by {message.author.name})')
        self.pending_cache[msg] = {'request': message, 'title': title, 'body': f'💡 {title} 💡\n\nSuggested by {message.author.name}'}
        await msg.add_reaction('🆗')
        await msg.add_reaction('🚫')
        return


