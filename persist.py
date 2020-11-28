import aiosqlite
import logging


class DatabaseManager():
    def __init__(self, sqlite3_file):
        self.dbpath = sqlite3_file
    

    async def initialize(self):
        async with aiosqlite.connect(self.dbpath) as db:
            logging.info("Connecting to and preparing SQLITE database...")
            await db.execute('CREATE TABLE IF NOT EXISTS CACHED_TWEETS (tweet_id varchar(255), channel_id varchar(255), UNIQUE(tweet_id, channel_id))')
            logging.info("Done.")


    async def add_tweet(self, tweet_id, channel_id):
        async with aiosqlite.connect(self.dbpath) as db:
            await db.execute(f"INSERT OR IGNORE INTO CACHED_TWEETS (tweet_id, channel_id) VALUES ('{tweet_id}', '{channel_id}')")
            await db.commit()
        return True


    async def already_seen(self, tweet_id, channel_id):
        async with aiosqlite.connect(self.dbpath) as db:
            async with db.execute(f"SELECT * FROM CACHED_TWEETS WHERE tweet_id = '{tweet_id}' AND channel_id = '{channel_id}'") as cursor:
                return await cursor.fetchone() != None
        return False


