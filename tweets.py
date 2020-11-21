import json
import asyncio

import requests
import discord

TWEET_LOOKBACK = 5
TWITTER_API_RECENT_ENDPOINT = 'https://api.twitter.com/2/tweets/search/recent?'


class DatabaseManager():
    def __init__(self, db, bearer_token):
        self.db = db
        self.bearer_token = bearer_token
        self.last_seen_tweet_cache = {}
        self.self.relay_map = {'DragonRealms': '714916798411309067'}
        self.tasks = []


    async def initialize(self):
        print("Initializing tweet watching...")
        # For each twitter account, create a new async task that will run polling tweets
        for account in self.relay_map.keys():
            print(f"  Watching @{account}...")
            self.tasks.append(asyncio.create_task(self.poll_tweets(account)))
        print("Done.")


    async def poll_tweets(self, account):
        # Set the bearer
        header = {'authorization': f'Bearer {self.bearer_token}'}

        while True:
            # Register the event location for relaying tweets - unique per twitter account and channel)
            destination_channel_id = self.relay_map[account]
            tweet_cache_key = f'{account}|{destination_channel_id}'
            first_poll = tweet_cache_key in self.last_seen_tweet_cache

            params = {'query': f'from:{account}'}
            if not first_poll:
                params['since_id'] = self.last_seen_tweet_cache[tweet_cache_key]

            r = requests.get(TWITTER_API_RECENT_ENDPOINT, params=params, headers=header)
            content = r.content.decode('utf-8')
            d = json.loads(content)
            
            tweets = []
            if d['meta']['result_count'] > 0:
                tweets = d['data']
                self.last_seen_tweet_cache[tweet_cache_key] = tweets[0]['id']
                print(f'fetched {len(tweets)} new tweets')

            # On bot startup, look back a certain number of tweets
            if len(tweets) > TWEET_LOOKBACK:
                tweets = tweets[:TWEET_LOOKBACK]
            
            # Iterate through the tweets, from oldest to newest
            for tweet in tweets[::-1]:
                # If we've relayed this tweet before, skip it
                if await self.db.already_seen(tweet["id"], destination_channel_id):
                    print(f"Skipping cached tweet {tweet['id']}")
                    continue
                
                # Share the tweet with the channel, and cache it
                channel = discord.utils.get(self.get_all_channels(), id=int(destination_channel_id))
                print(f"Relaying tweet {tweet['id']}")
                await channel.send(f'https://twitter.com/{account}/status/{tweet["id"]}')
                await self.db.add_tweet(tweet["id"], destination_channel_id)
            
            await asyncio.sleep(30)
