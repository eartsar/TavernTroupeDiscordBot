import argparse
import requests
import yaml
import sys
import os
import json
import re
import discord
import requests
import asyncio

from persist import DatabaseManager

RELAY_MAP = {'DragonRealms': '714916798411309067'}
LAST_SEEN_MAP = {}


parser = argparse.ArgumentParser(description='Run the TroupeTweets bot.')
parser.add_argument('--config', type=str, required=True, 
                    help='The path to the configuration yml.')
args = parser.parse_args()


# Load the config file
config = {}
with open(args.config, 'r') as f:
    config = yaml.safe_load(f)

BEARER_TOKEN = config['bearer_token']
BOT_TOKEN = config['bot_token']

DB_FILE_PATH = config['sqlite3_database_path']



class TroupeTweetBot(discord.Client):
    def __init__(self):
        self.db = DatabaseManager(DB_FILE_PATH)
        self.tasks = []
        super().__init__()


    async def on_ready(self):
        print("Ready to rumble.")
        await self.db.initialize()
        self.tasks.append(asyncio.create_task(self.heartbeat()))

        
    async def heartbeat(self):
        # Get all the tweets from dragonrealms
        header = {}
        header['authorization'] = f'Bearer {BEARER_TOKEN}'

        while True:
            for account in RELAY_MAP.keys():
                seen_key = f'{account}|{RELAY_MAP[account]}'

                tweets = []
                if seen_key not in LAST_SEEN_MAP:
                    r = requests.get(f'https://api.twitter.com/2/tweets/search/recent?query=from:{account}', headers=header)
                    content = r.content.decode('utf-8')
                    d = json.loads(content)
                    tweets = d['data']
                    LAST_SEEN_MAP[seen_key] = tweets[0]['id']
                else:
                    last_id = LAST_SEEN_MAP[seen_key]
                    r = requests.get(f'https://api.twitter.com/2/tweets/search/recent?since_id={last_id}&query=from:{account}', headers=header)
                    content = r.content.decode('utf-8')
                    d = json.loads(content)
                    if d['meta']['result_count'] > 0:
                        tweets = d['data']
                        LAST_SEEN_MAP[seen_key] = tweets[0]['id']
                        print(f'fetched {len(tweets)} new tweets')

                # Reverse the tweets to be the last 10 tweets, with most recent last
                if len(tweets) > 5:
                    tweets = tweets[:5]
                
                for tweet in tweets[::-1]:
                    # If we've relayed this tweet before, don't bother doing so again
                    if await self.db.already_seen(tweet["id"], RELAY_MAP[account]):
                        print(f"Skipping cached tweet {tweet['id']}")
                        continue
                    # Share the tweet with the channel, and cache it
                    channel = discord.utils.get(self.get_all_channels(), id=int(RELAY_MAP[account]))
                    print(f"Relaying tweet {tweet['id']}")
                    await channel.send(f'https://twitter.com/DragonRealms/status/{tweet["id"]}')
                    await self.db.add_tweet(tweet["id"], RELAY_MAP[account])
            
            await asyncio.sleep(30)



def main():
    client = TroupeTweetBot()
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
