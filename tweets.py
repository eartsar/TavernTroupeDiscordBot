import json
import asyncio
import logging

import requests
import discord

TWEET_LOOKBACK = 5
TWITTER_API_RECENT_ENDPOINT = 'https://api.twitter.com/2/tweets/search/recent?'


class TweetManager():
    def __init__(self, bot, db, bearer_token, relay_map):
        self.bot = bot
        self.db = db
        self.bearer_token = bearer_token
        self.last_seen_tweet_cache = {}
        self.relay_map = relay_map
        self.tasks = []


    async def initialize(self):
        logging.info("Initializing tweet watching...")
        # For each twitter account, create a new async task that will run polling tweets
        for account in self.relay_map.keys():
            logging.info(f"\tWatching @{account}...")
            self.tasks.append(asyncio.create_task(self.poll_tweets(account)))

        for destination_channel_id in self.relay_map[account]:
            channel = discord.utils.get(self.bot.get_all_channels(), id=int(destination_channel_id))
            if not channel:
                logging.warning(f"Bot does not have access to channel with ID {destination_channel_id}!")
        logging.info("Done.")


    async def poll_tweets_for_channel(self, account, destination_channel_id):
        try:
            header = {'authorization': f'Bearer {self.bearer_token}'}
            tweet_cache_key = f'{account}|{destination_channel_id}'

            params = {'query': f'from:{account}'}

            waking_up = tweet_cache_key not in self.last_seen_tweet_cache
            if not waking_up and self.last_seen_tweet_cache[tweet_cache_key]:
                params['since_id'] = self.last_seen_tweet_cache[tweet_cache_key]


            r = requests.get(TWITTER_API_RECENT_ENDPOINT, params=params, headers=header)
            content = r.content.decode('utf-8')
            d = json.loads(content)

            # If the since_id was invalid (over a week old) remove the param, and re-do the request
            if not waking_up and 'errors' in d and d['errors']:
            	for error in d['errors']:
            		if 'since_id' in error['parameters'] and self.last_seen_tweet_cache[tweet_cache_key] in error['parameters']['since_id']:
            			del params['since_id']
            			self.last_seen_tweet_cache[tweet_cache_key] = None
            			r = requests.get(TWITTER_API_RECENT_ENDPOINT, params=params, headers=header)
            			content = r.content.decode('utf-8')
            			d = json.loads(content)
            			break
            
            tweets = []
            if d['meta']['result_count'] > 0:
                tweets = d['data']
                self.last_seen_tweet_cache[tweet_cache_key] = tweets[0]['id']

            # On bot startup, look back a certain number of tweets
            if len(tweets) > TWEET_LOOKBACK:
                tweets = tweets[:TWEET_LOOKBACK]

            # Grab the channel
            channel = discord.utils.get(self.bot.get_all_channels(), id=int(destination_channel_id))
            if not channel:
                return

            tweets = tweets[::-1]
            # If we've relayed this tweet before, skip it
            new_tweets = [tweet for tweet in tweets if not await self.db.already_seen(tweet["id"], destination_channel_id)]
            
            if waking_up and len(new_tweets) > 0:
                await channel.send("💤 ...! That was a nice nap 😹... I *may* have forgotten to tell you about these tweets...")

            # Iterate through the tweets, from oldest to newest
            for tweet in new_tweets:
                # Share the tweet with the channel, and cache it
                logging.info(f"New tweet: {tweet['id']} --> {destination_channel_id}")
                await channel.send(f'https://twitter.com/{account}/status/{tweet["id"]}')
                await self.db.add_tweet(tweet["id"], destination_channel_id)
        except Exception as e:
            logging.exception(f'Exception thrown while attempting to poll tweets for channel {destination_channel_id}')


    async def poll_tweets(self, account):
        while True:
            # Register the event location for relaying tweets - unique per twitter account and channel)
            for destination_channel_id in self.relay_map[account]:
                await self.poll_tweets_for_channel(account, destination_channel_id)
            await asyncio.sleep(30)
        
