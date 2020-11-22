import argparse

import yaml
import discord
import asyncio
import logging

from persist import DatabaseManager
from tweets import TweetManager
from reminders import ReminderManager

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

TWITTER_BEARER_TOKEN = config['twitter_bearer_token']
TWITTER_RELAY_MAP = config['twitter_relay_map']
BOT_TOKEN = config['bot_token']
DB_FILE_PATH = config['sqlite3_database_path']
GOOGLE_CAL_CREDS = config['google_credentials']
REMINDER_RELAY_MAP = config['reminder_relay_map']



class TroupeTweetBot(discord.Client):
    def __init__(self):
        self.db = DatabaseManager(DB_FILE_PATH)
        self.tweets = TweetManager(self, self.db, TWITTER_BEARER_TOKEN, TWITTER_RELAY_MAP)
        self.reminders = ReminderManager(self, GOOGLE_CAL_CREDS, REMINDER_RELAY_MAP)
        super().__init__()


    async def on_ready(self):
        logging.info("TroupeBot initializing...")
        await self.db.initialize()
        await self.tweets.initialize()
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
        elif message.content.startswith('!events'):
            tokens = message.content.split(' ')
            name = tokens[1] if len(tokens) > 1 else None
            await self.reminders.get_upcoming_events(message.channel, calendar_name=name)
        elif message.content.startswith('!help'):
            await message.channel.send('''😽  Here's what I know how to do (so far)!
```\
!ping                           Test command to ensure the bot is healthy.
!events <calendar_name>         Pull up the events for the named calendar for this month and next month
                                Keep calendar_name blank to get a list of calendars the bot knows of.
!help\
```''')


def main():
    client = TroupeTweetBot()
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
