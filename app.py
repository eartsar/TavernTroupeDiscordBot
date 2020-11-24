import argparse

import yaml
import discord
import asyncio
import logging

from persist import DatabaseManager
from tweets import TweetManager
from reminders import ReminderManager
from drlogger import DRLoggerManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# Parse the command line arguments
parser = argparse.ArgumentParser(description='Run the TroupeTweets bot.')
parser.add_argument('--config', type=str, required=True, help='The path to the configuration yaml file.')
args = parser.parse_args()


# Load the configuration file
config = {}
with open(args.config, 'r') as f:
    config = yaml.safe_load(f)

BOT_TOKEN = config['bot_token']
DB_FILE_PATH = config['sqlite3_database_path']
CALENDAR_ENABLED = config['enable_calendar']
TWITTER_ENABLED = config['enable_twitter']
DRLOGGER_ENABLED = config['enable_drlogger']

TWITTER_BEARER_TOKEN = config['twitter_bearer_token'] if TWITTER_ENABLED else None
TWITTER_RELAY_MAP = config['twitter_relay_map'] if TWITTER_ENABLED else None

GOOGLE_CAL_CREDS = config['google_credentials'] if CALENDAR_ENABLED else None
REMINDER_RELAY_MAP = config['reminder_relay_map'] if CALENDAR_ENABLED else None

DR_ACCOUNT_INFO = config['dr_account'] if DRLOGGER_ENABLED else None
DRLOG_AUTHORIZED_USER_IDS = config['log_authorized_users'] if DRLOGGER_ENABLED else None
DRLOG_UPLOAD_CHANNEL_ID = config['log_upload_channel'] if DRLOGGER_ENABLED else None
DRLOG_FILENAME_PREFIX = config['log_filename_prefix'] if DRLOGGER_ENABLED else None



class TroupeTweetBot(discord.Client):
    def __init__(self):
        self.db = DatabaseManager(DB_FILE_PATH)
        self.tweets = TweetManager(self, self.db, TWITTER_BEARER_TOKEN, TWITTER_RELAY_MAP)
        self.reminders = ReminderManager(self, GOOGLE_CAL_CREDS, REMINDER_RELAY_MAP)
        self.drlogger = DRLoggerManager(self, DR_ACCOUNT_INFO, DRLOG_UPLOAD_CHANNEL_ID, DRLOG_FILENAME_PREFIX)
        super().__init__()


    async def on_ready(self):
        logging.info("TroupeBot initializing...")
        await self.db.initialize()
        if TWITTER_ENABLED:
            await self.tweets.initialize()
        if CALENDAR_ENABLED:
            await self.reminders.initialize()


    async def on_message(self, message):
        # Bot ignores itself. This is how you avoid the singularity.
        if message.author == self.user:
            return

        # Ignore anything that doesn't start with the magic token
        if not message.content.startswith('!'):
            return

        if message.content == '!ping':
            await message.channel.send(f'{message.author.mention} pong!')
        elif CALENDAR_ENABLED and message.content.startswith('!events'):
            tokens = message.content.split(' ')
            name = tokens[1].strip() if len(tokens) > 1 else None
            await self.reminders.get_upcoming_events(message.channel, calendar_name=name)
        elif DRLOGGER_ENABLED and message.content.startswith('!log'):
            tokens = message.content.split(' ')
            name = tokens[1].strip() if len(tokens) > 1 else None
            if message.author.id not in DRLOG_AUTHORIZED_USER_IDS:
                await message.channel.send('😾  You aren\'t allowed to do this! You\'ll have to ask the speakers to do this!')
            elif name == 'start':
                await self.drlogger.start(message.channel)
            elif name == 'stop':
                await self.drlogger.stop(message.channel)
            else:
                await message.channel.send('😾  You can start or stop logging with `!log <start|stop>`.')
        elif message.content.startswith('!help'):
            await message.channel.send('''😽  Here's what I know how to do (so far)!
```\
!ping                           Test command to ensure the bot is healthy.
!events <calendar_name>         Pull up the events for the named calendar for this month and next month
                                Keep calendar_name blank to get a list of calendars the bot knows of.
!log <start|stop>               Tells the troupe scribe to start or stop their note-taking (requires permission).
!help\
```''')


def main():
    client = TroupeTweetBot()
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
