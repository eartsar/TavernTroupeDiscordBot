import discord
import pytube
import asyncio
import shutil


DISALLOW_MESSAGE = 'these commands are only usable in #music-channel'


class MusicManager():
    def __init__(self, bot, music_text_channel_id, music_voice_channel_id):
        self.bot = bot
        self.music_text_channel_id = music_text_channel_id
        self.music_voice_channel_id = music_voice_channel_id


    def _download(self, url):
        return pytube.YouTube(url).streams.first().download()


    async def play(self, message, url):
        if message.channel.id != self.music_text_channel_id:
            return await message.channel.send(f'{message.author.mention}, {DISALLOW_MESSAGE}')

        downloaded_name = await asyncio.get_event_loop().run_in_executor(None, self._download, url)
        temp_name = '.song.mp4'
        shutil.move(downloaded_name, temp_name)

        channel = discord.utils.find(lambda channel: channel.id == self.music_voice_channel_id, message.guild.voice_channels)
        await channel.connect()

        voice = discord.utils.get(self.bot.voice_clients, guild=message.guild)
        if not voice.is_playing():
            voice.play(discord.FFmpegPCMAudio(temp_name))
            voice.is_playing()
            await message.reply(f"Now playing your song request!")
        else:
            await message.channel.send("Already playing a song!")
        
        return


    async def stop(self, message):
        if message.channel.id != self.music_text_channel_id:
            return await message.channel.send(f'{message.author.mention}, {DISALLOW_MESSAGE}')

        voice = discord.utils.get(self.bot.voice_clients, guild=message.guild)
        if voice.is_playing():
            return await voice.disconnect()
        else:
            await message.channel.send("No song is playing!")



