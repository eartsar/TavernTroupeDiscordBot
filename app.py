import argparse
import re
import logging
import asyncio
import subprocess

import yaml
import discord
import requests

from persist import DatabaseManager
from tweets import TweetManager
from reminders import ReminderManager
from drlogger import DRLoggerManager
from photos import PhotosManager


from util import ValueRetainingRegexMatcher


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
DRLOGGER_ENABLED = config['enable_drlogger']

TWITTER_BEARER_TOKEN = config['twitter_bearer_token'] if TWITTER_ENABLED else None
TWITTER_RELAY_MAP = config['twitter_relay_map'] if TWITTER_ENABLED else None

GOOGLE_CAL_CREDS = config['google_credentials'] if CALENDAR_ENABLED else None
REMINDER_RELAY_MAP = config['reminder_relay_map'] if CALENDAR_ENABLED else None

PETPIC_ROOT_PATH = config['petpic_root_path'] if PETPIC_ENABLED else None

DR_ACCOUNT_INFO = config['dr_account'] if DRLOGGER_ENABLED else None
DRLOG_AUTHORIZED_USER_IDS = config['log_authorized_users'] if DRLOGGER_ENABLED else None
DRLOG_UPLOAD_CHANNEL_ID = config['log_upload_channel'] if DRLOGGER_ENABLED else None
DRLOG_FILENAME_PREFIX = config['log_filename_prefix'] if DRLOGGER_ENABLED else None


SIGNATURE_EMOJI = '<:wafflebot:780940516140515359>'


HELP_TEXT = '''\
 BOT UTILITY FUNCTIONS
-----------------------
!ping                           Test command to ensure the bot is healthy
!help                           Displays this message
!version [history]              Displays recent changes

     FUN FUNCTIONS
-----------------------
!nice                           Having a rough day? I'll say something nice!
!joke                           ...Or tell you a joke!

   PETPIC FUNCTIONS
-----------------------
!petpic upload <album> [url]    Upload a picture to a pet album. This must be the comment on a file upload to the bot
                                    url - must be a public link to a zip on Google Drive or Dropbox or wget-able file
!petpic random [album]          Show a random pet picture
!petpic list [all]              Shows a list of your albums, or everyone's albums
!petpic create [name]           Create a new album for a pet

                                THE COMMANDS BELOW CANNOT BE UNDONE!
!petpic share [name]            Give up ownership and make an album public
!petpic delete [name]           Delete an album (does NOT delete files on server)
!petpic wipe                    Delete ALL your petpic stuff from server database and disk

   HELPFUL FUNCTIONS
-----------------------
!events <calendar_name>         Pull up the events for the named calendar for this month and next month.
!log <start|stop>               Tells the troupe scribe to start or stop their note-taking (requires permission).'''


# Command regex
PING_REGEX = re.compile(r'!ping')
CALENDAR_REGEX = re.compile(r'!events(?: (.+))')
DRLOGGER_REGEX = re.compile(r'!log (start|stop)')
NICE_REGEX = re.compile(r'!nice')
JOKE_REGEX = re.compile(r'!joke')
HELP_REGEX = re.compile(r'!help')
PETPIC_REGEX = re.compile(r'!petpic (add|create|delete|list|random|remove|upload|wipe|share)(?: ([^\s\\]+))?(?: (.+))?')
VERSION_REGEX = re.compile(r'!version(?: (.+))?')

class TroupeTweetBot(discord.Client):
    def __init__(self):
        self.db = DatabaseManager(DB_FILE_PATH)
        self.tweets = TweetManager(self, self.db, TWITTER_BEARER_TOKEN, TWITTER_RELAY_MAP)
        self.reminders = ReminderManager(self, GOOGLE_CAL_CREDS, REMINDER_RELAY_MAP)
        self.drlogger = DRLoggerManager(self, DR_ACCOUNT_INFO, DRLOG_UPLOAD_CHANNEL_ID, DRLOG_FILENAME_PREFIX)
        self.pics = PhotosManager(self, self.db, PETPIC_ROOT_PATH)
        super().__init__()


    async def on_ready(self):
        logging.info("TroupeBot initializing...")
        await self.db.initialize()
        if TWITTER_ENABLED:
            await self.tweets.initialize()
        if CALENDAR_ENABLED:
            await self.reminders.initialize()
        if PETPIC_ENABLED:
            await self.pics.initialize()


    async def on_reaction_add(self, reaction, user):
        # If the reaction was from this bot, ignore it
        if user == self.user:
            return
        
        # If the reaction was to a "roll build" comment, and the reactor is the owner of it...
        if reaction.message.author.id == self.user.id:
            await self.pics.reaction_handler(user, reaction)


    async def on_message(self, message):
        # Bot ignores itself. This is how you avoid the singularity.
        if message.author == self.user:
            return

        # Ignore anything that doesn't start with the magic token
        if not message.content.startswith('!'):
            return

        # Match against the right command, grab args, and go
        m = ValueRetainingRegexMatcher(message.content)

        # Process a command
        if m.match(PING_REGEX):
            await message.channel.send(f'{message.author.mention} pong!')
        elif m.match(CALENDAR_REGEX) and CALENDAR_ENABLED:
            name = m.group(1) if m.group(1) else None
            await self.reminders.get_upcoming_events(message.channel, calendar_name=name)
        elif m.match(DRLOGGER_REGEX) and DRLOGGER_ENABLED:
            cmd = m.group(1) if m.group(1) else None
            if message.author.id not in DRLOG_AUTHORIZED_USER_IDS:
                return await message.channel.send('😾  You aren\'t allowed to do this! You\'ll have to ask the speakers to do this!')
            elif cmd == 'start':
                await self.drlogger.start(message.channel)
            elif cmd == 'stop':
                await self.drlogger.stop(message.channel)
            else:
                await message.channel.send('😾  You can start or stop logging with `!log <start|stop>`.')
        elif m.match(NICE_REGEX) and FUN_ENABLED:
            compliment = requests.get('https://complimentr.com/api').json()['compliment']
            await message.channel.send(f"{SIGNATURE_EMOJI} {compliment}")
        elif m.match(JOKE_REGEX) and FUN_ENABLED:
            joke = requests.get('https://official-joke-api.appspot.com/jokes/random').json()
            await message.channel.send(f"{SIGNATURE_EMOJI} {joke['setup']}\n    ...{joke['punchline']}")
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
        elif m.match(HELP_REGEX):
            await message.channel.send(f"😽  Here's what I know how to do (so far)!\n```{HELP_TEXT}```")



def main():
    client = TroupeTweetBot()
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
