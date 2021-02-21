import discord
import youtube_dl
import asyncio
import shutil
import os

DISALLOW_MESSAGE = 'these commands are only usable in #music-channel'
SONG_FILENAME = '.song.mp4'


class QueueItem():
    def __init__(self, message, url, data):
        self.message = message
        self.url = url
        self.data = data
        self.title = data['title']


class MusicManager():
    def __init__(self, bot, music_text_channel_id, music_voice_channel_id):
        self.bot = bot
        self.music_text_channel_id = music_text_channel_id
        self.music_voice_channel_id = music_voice_channel_id
        self.queue = []


    def _download(self, url):
        try:
            os.remove(SONG_FILENAME)
        except:
            pass

        ydl_opts = {'format': 'mp4', 'outtmpl': SONG_FILENAME, 'nooverwrites': False, 'audioquality': '128K'}
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])


    async def play(self, message, url, called_internally=False):
        if message.channel.id != self.music_text_channel_id:
            return await message.channel.send(f'{message.author.mention}, {DISALLOW_MESSAGE}')

        data = None
        with youtube_dl.YoutubeDL({}) as ydl:
            data = ydl.extract_info(url, download=False)
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.listening,
                    name=data['title'],
                    url=url,
                    small_image_url=data['thumbnail']
                )
            )

        # If this was called by a normal user, add the song to the queue, and bail if we're currently playing
        if not called_internally:
            self.queue.append(QueueItem(message, url, data))
            if len(self.queue) > 1:
                return await message.channel.send("Song added to the queue.")

        await asyncio.get_event_loop().run_in_executor(None, self._download, url)

        channel = discord.utils.find(lambda channel: channel.id == self.music_voice_channel_id, message.guild.voice_channels)
        try:
            await channel.connect()
        except discord.errors.ClientException:
            pass
        

        def finish_playing(error):
            # It's possible that we interrupted playing, and that this hook still gets called
            if self.queue:
                self.queue.pop(0)
            
            try:
                os.remove(SONG_FILENAME)
            except:
                pass

            if self.queue:
                coro = self.play(self.queue[0].message, self.queue[0].url, called_internally=True)
                asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            else:
                coro = voice.disconnect()
                asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

                coro = self.bot.change_presence(status=discord.Status.online, activity=None)
                asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

        
        voice = discord.utils.get(self.bot.voice_clients, guild=message.guild)
        voice.play(discord.FFmpegOpusAudio(SONG_FILENAME, bitrate=128), after=finish_playing)
        voice.is_playing()
        return await message.reply(f"😸 🎵  **Now playing:** *{data['title']}*  🎵")


    async def stop(self, message):
        if message.channel.id != self.music_text_channel_id:
            return await message.channel.send(f'{message.author.mention}, {DISALLOW_MESSAGE}')

        voice = discord.utils.get(self.bot.voice_clients, guild=message.guild)
        if voice.is_playing() or voice.is_connected() or self.queue:
            try:
                os.remove(SONG_FILENAME)
            except Exception:
                pass

            self.queue = []
            await self.bot.change_presence(status=discord.Status.online, activity=None)
            return await voice.disconnect()
        else:
            await message.channel.send("No song is playing!")


    async def peek(self, message):
        if message.channel.id != self.music_text_channel_id:
            return await message.channel.send(f'{message.author.mention}, {DISALLOW_MESSAGE}')

        if not self.queue:
            return await message.channel.send("No song is playing!")

        now_playing = f'>>> `NOW PLAYING` - **{self.queue[0].title}**'
        up_next = '\n\nUp next...\n' + '\n'.join([_.title for _ in self.queue[1:]]) if len(self.queue) > 1 else ''
        return await message.channel.send(f'Here are the upcoming songs...\n{now_playing}{up_next}', embed=None)

