import discord
import youtube_dl
import asyncio
import shutil
import os

DISALLOW_MESSAGE = 'these commands are only usable in #music-channel'
SONG_FILENAME = '.song.mp4'


class MusicManager():
    def __init__(self, bot, music_text_channel_id, music_voice_channel_id):
        self.bot = bot
        self.music_text_channel_id = music_text_channel_id
        self.music_voice_channel_id = music_voice_channel_id


    def _download(self, url):
        ydl_opts = {'format': 'mp4', 'outtmpl': SONG_FILENAME, 'nooverwrites': False}
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])


    async def play(self, message, url):
        if message.channel.id != self.music_text_channel_id:
            return await message.channel.send(f'{message.author.mention}, {DISALLOW_MESSAGE}')

        await asyncio.get_event_loop().run_in_executor(None, self._download, url)

        channel = discord.utils.find(lambda channel: channel.id == self.music_voice_channel_id, message.guild.voice_channels)
        await channel.connect()

        voice = discord.utils.get(self.bot.voice_clients, guild=message.guild)

        def finish_playing(error):
            coro = voice.disconnect()
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            try:
                os.remove(SONG_FILENAME)
            except:
                pass

        if not voice.is_playing():
            voice.play(discord.FFmpegPCMAudio(SONG_FILENAME), after=finish_playing)
            voice.is_playing()
            await message.reply(f"Now playing your song request!")
        else:
            await message.channel.send("Already playing a song!")
        
        return


    async def stop(self, message):
        if message.channel.id != self.music_text_channel_id:
            return await message.channel.send(f'{message.author.mention}, {DISALLOW_MESSAGE}')

        voice = discord.utils.get(self.bot.voice_clients, guild=message.guild)
        if voice.is_playing() or voice.is_connected():
            try:
                os.remove(SONG_FILENAME)
            except Exception:
                pass

            return await voice.disconnect()
        else:
            await message.channel.send("No song is playing!")
