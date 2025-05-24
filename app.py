import argparse
import re
import logging
import asyncio
import subprocess
import socket
import yaml
import discord
import requests

from persist import DatabaseManager
from tweets import TweetManager
from reminders import ReminderManager
from drlogger import DRLoggerManager
from photos import PhotosManager
from fun import FunManager
from music import MusicManager
from idea import IdeaManager

from util import ValueRetainingRegexMatcher

from google import genai
from google.genai import types

# Parse the command line arguments
parser = argparse.ArgumentParser(description='Run the TroupeTweets bot.')
parser.add_argument('--config', type=str, required=True, help='The path to the configuration yaml file.')
args = parser.parse_args()


# Load the configuration file
config = {}
with open(args.config, 'r') as f:
    config = yaml.safe_load(f)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(config['logging_path'] if 'logging_path' in config else 'bot.log'),
        logging.StreamHandler()
    ]
)

logging.info("Loading configuration...")
BOT_TOKEN = config['bot_token']
DB_FILE_PATH = config['sqlite3_database_path']
CALENDAR_ENABLED = config['enable_calendar']
TWITTER_ENABLED = config['enable_twitter']
PETPIC_ENABLED = config['enable_petpic']
FUN_ENABLED = config['enable_fun']
MUSIC_ENABLED = config['enable_music']
IDEA_ENABLED = config['enable_idea']
DRLOGGER_ENABLED = config['enable_drlogger']

# If in-game logging is enabled, disable everything else.
if DRLOGGER_ENABLED:
    CALENDAR_ENABLED = False
    TWITTER_ENABLED = False
    PETPIC_ENABLED = False
    FUN_ENABLED = False
    MUSIC_ENABLED = False
    IDEA_ENABLED = False

TWITTER_BEARER_TOKEN = config['twitter_bearer_token'] if TWITTER_ENABLED else None
TWITTER_RELAY_MAP = config['twitter_relay_map'] if TWITTER_ENABLED else None

GOOGLE_CAL_CREDS = config['google_credentials'] if CALENDAR_ENABLED else None
REMINDER_RELAY_MAP = config['reminder_relay_map'] if CALENDAR_ENABLED else None

PETPIC_ROOT_PATH = config['petpic_root_path'] if PETPIC_ENABLED else None

DR_ACCOUNT_INFO = config['dr_account'] if DRLOGGER_ENABLED else None
DRLOG_AUTHORIZED_USER_IDS = config['log_authorized_users'] if DRLOGGER_ENABLED else None
DRLOG_UPLOAD_CHANNEL_ID = config['log_upload_channel'] if DRLOGGER_ENABLED else None
DRLOG_FILENAME_PREFIX = config['log_filename_prefix'] if DRLOGGER_ENABLED else None

NAUGHTY_CHANNEL_IDS = config['naughty_channels'] if FUN_ENABLED and 'naughty_channels' in config and config['naughty_channels'] else []

MUSIC_TEXT_CHANNEL_ID = config['music_text_channel'] if MUSIC_ENABLED else None
MUSIC_VOICE_CHANNEL_ID = config['music_voice_channel'] if MUSIC_ENABLED else None

GITHUB_TOKEN = config['github_token'] if IDEA_ENABLED else None
MAINTAINER_ID = config['maintainer_id'] if IDEA_ENABLED else None

GEMINI_KEY = config['gemini_key']

SIGNATURE_EMOJI = '<:wafflebot:780940516140515359>'


HELP_TEXT = '''\
 BOT UTILITY FUNCTIONS
-----------------------
!ping                           Checks if online
!help                           Displays this message
!version [history]
!idea <description>             Submit an idea for the bot's maintainer to implement.

     FUN FUNCTIONS
-----------------------
!nice                           Wafflebot compliments you.
!joke                             or makes you laugh
!riddle                           or confounds you
!roast                          When Pistol lies, do this 🤏, and fig me like the bragging Spaniard!

    MUSIC FUNCTIONS
-----------------------
!music play <url>               Play youtube url in the voice channel
!music stop

   PETPIC FUNCTIONS
-----------------------
!petpic upload <album> [url]    Upload a picture to an album. This must be the comment on a picture uploaded!
                                  url - a public url to a zip on Google Drive or Dropbox

!petpic random [album]
!petpic list [all]              List albums
!petpic create [name]           Create a new album

                                THE COMMANDS BELOW CANNOT BE UNDONE!
!petpic share [name]            Give up ownership and make an album public
!petpic delete [name]           Delete an album (does NOT delete files on server)
!petpic wipe                    Delete ALL data and files you uplaoded

    OTHER FUNCTIONS
-----------------------
!events <calendar_name>         Show upcoming events for a calendar
!log <start|stop>               Tells the troupe scribe to start or stop their note-taking (requires permission).'''


# Command regex
PING_REGEX = re.compile(r'!ping')
CALENDAR_REGEX = re.compile(r'!events(?: (.+))')
DRLOGGER_REGEX = re.compile(r'!log (start|stop)')
NICE_REGEX = re.compile(r'!nice')
JOKE_REGEX = re.compile(r'!joke')
RIDDLE_REGEX = re.compile(r'!riddle')
ROAST_REGEX = re.compile(r'!roast')
HELP_REGEX = re.compile(r'!help')
MUSIC_REGEX = re.compile(r'!music (play|stop|queue|skip|peek|list)(?: (.+youtube.+))?')
PETPIC_REGEX = re.compile(r'!petpic (add|create|delete|list|random|remove|upload|wipe|share)(?: ([^\s\\]+))?(?: (.+))?')
VERSION_REGEX = re.compile(r'!version(?: (.+))?')
IDEA_REGEX = re.compile(r'!idea (.+)')
SUMMARIZE_REGEX = re.compile(r'!summarize')

class TroupeTweetBot(discord.Client):
    def __init__(self, **kwargs):
        self.db = DatabaseManager(DB_FILE_PATH)
        self.tweets = TweetManager(self, self.db, TWITTER_BEARER_TOKEN, TWITTER_RELAY_MAP)
        self.reminders = ReminderManager(self, GOOGLE_CAL_CREDS, REMINDER_RELAY_MAP)
        self.drlogger = DRLoggerManager(self, DR_ACCOUNT_INFO, DRLOG_UPLOAD_CHANNEL_ID, DRLOG_FILENAME_PREFIX)
        self.pics = PhotosManager(self, self.db, PETPIC_ROOT_PATH)
        self.fun = FunManager(self, NAUGHTY_CHANNEL_IDS)
        self.music = MusicManager(self, MUSIC_TEXT_CHANNEL_ID, MUSIC_VOICE_CHANNEL_ID)
        self.idea = IdeaManager(self, GITHUB_TOKEN, MAINTAINER_ID)
        self.initialized = False
        super().__init__(**kwargs)


    async def on_ready(self):
        if not self.initialized:
            logging.info("TroupeBot initializing...")
            if not DRLOGGER_ENABLED:
                await self.db.initialize()
            if TWITTER_ENABLED:
                await self.tweets.initialize()
            if CALENDAR_ENABLED:
                await self.reminders.initialize()
            if PETPIC_ENABLED:
                await self.pics.initialize()
            self.initialized = True


    async def on_reaction_add(self, reaction, user):
        # Logging instances don't respond to reactions
        if DRLOGGER_ENABLED:
            return

        # If the reaction was from this bot, ignore it
        if user == self.user:
            return
        
        # If the reaction was to a bot message, call the various handlers
        if reaction.message.author.id == self.user.id:
            await self.pics.reaction_handler(user, reaction)
            await self.idea.reaction_handler(user, reaction)


    async def on_message(self, message):
        # Bot ignores itself. This is how you avoid the singularity.
        if message.author == self.user:
            return

        # Ignore anything that doesn't start with the magic token
        if not message.content.startswith('!'):
            return

        # Match against the right command, grab args, and go
        m = ValueRetainingRegexMatcher(message.content)

        # Handle ping separately, no matter the instance
        if m.match(PING_REGEX):
            return await message.channel.send(f'{message.author.mention} {socket.gethostname()} ({"non-" if not DRLOGGER_ENABLED else ""}logger)": pong!')

        # DRLOGGER instances only handle DRLOGGER commands
        if DRLOGGER_ENABLED and m.match(DRLOGGER_REGEX):
            cmd = m.group(1) if m.group(1) else None
            if message.author.id not in DRLOG_AUTHORIZED_USER_IDS:
                await message.channel.send('😾  You aren\'t allowed to do this! You\'ll have to ask the speakers to do this!')
            elif cmd == 'start':
                await self.drlogger.start(message.channel)
            elif cmd == 'stop':
                await self.drlogger.stop(message.channel)
            else:
                await message.channel.send('😾  You can start or stop logging with `!log <start|stop>`.')
            
            return
        elif DRLOGGER_ENABLED:
            return
        
        # non-DRLOGGER instances do nothing for DRLOGGER commands
        if not DRLOGGER_ENABLED and m.match(DRLOGGER_REGEX):
            return

        # Process other commands
        if m.match(CALENDAR_REGEX) and CALENDAR_ENABLED:
            name = m.group(1) if m.group(1) else None
            await self.reminders.get_upcoming_events(message.channel, calendar_name=name)
        elif m.match(DRLOGGER_REGEX) and DRLOGGER_ENABLED:
            await message.channel.send('😾  You can start or stop logging with `!log <start|stop>`.')
        elif m.match(NICE_REGEX) and FUN_ENABLED:
            await self.fun.compliment(message)
        elif m.match(JOKE_REGEX) and FUN_ENABLED:
            await self.fun.joke(message)
        elif m.match(RIDDLE_REGEX) and FUN_ENABLED:
            await self.fun.riddle(message)
        elif m.match(ROAST_REGEX) and FUN_ENABLED:
            await self.fun.roast(message)
        elif m.match(PETPIC_REGEX) and PETPIC_ENABLED:
            cmd = m.group(1)
            album_name = m.group(2).lower() if m.group(2) else None
            if cmd in ('add', 'upload') and album_name:
                url = m.group(3) if m.group(3) else None
                await self.pics.upload(message, album_name, url)
            elif cmd == 'random':
                await self.pics.fetch(message, album_name)
            elif cmd == 'list':
                all_albums = m.group(2).lower() == 'all' if m.group(2) else False
                await self.pics.list_albums(message, all_albums=all_albums)
            elif cmd == 'create' and album_name:
                await self.pics.create_album(message, album_name)
            elif cmd in ('delete', 'remove') and album_name:
                await self.pics.delete_album(message, album_name)
            elif cmd == 'wipe':
                await self.pics.wipe(message)
            elif cmd == 'share' and album_name:
                await self.pics.share_album(message, album_name)
            else:
                await message.channel.send('😾  Not like this! Check `!help` for details on how to use `!petpic`.')
        elif m.match(VERSION_REGEX):
            num_commits = 5 if m.group(1) else 1
            version_content = subprocess.check_output(['git', 'log', '--use-mailmap', f'-n{num_commits}'])
            await message.channel.send("😸  💬   I'm the best version of myself, just like my dad taught me to be!" + 
                "\n```" + str(version_content, 'utf-8') + "```")
        elif m.match(MUSIC_REGEX) and MUSIC_ENABLED:
            command = m.group(1)
            url = m.group(2)
            if command == 'play' and url:
                await self.music.play(message, url)    
            elif command == 'stop':
                await self.music.stop(message)
            elif command in ('list', 'queue', 'peek'):
                await self.music.peek(message)
        elif m.match(IDEA_REGEX):
            await self.idea.submit(message, m.group(1))
        elif m.match(HELP_REGEX):
            await message.channel.send(f"😽  Here's what I know how to do (so far)!\n```{HELP_TEXT}```")
        elif m.match(SUMMARIZE_REGEX):
            if message.reference:
                attachment = message.reference.attachments[0]
                gemini_payload = await attachment.read()
                client = genai.Client(api_key=GEMINI_KEY)
                response = client.models.generate_content(
                    model="gemini-2.5-flash-preview-05-20",
                    contents=[gemini_payload, 'This is a log for a Tavern Troupe meeting. Summarize what happened.'])



def main():
    intents = discord.Intents.default()
    intents.members = True
    
    client = TroupeTweetBot(intents=intents)
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
