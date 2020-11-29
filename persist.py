import aiosqlite
import logging


class DatabaseManager():
    def __init__(self, sqlite3_file):
        self.dbpath = sqlite3_file
    

    async def initialize(self):
        async with aiosqlite.connect(self.dbpath) as db:
            logging.info("Connecting to and preparing SQLITE database...")
            await db.execute('CREATE TABLE IF NOT EXISTS CACHED_TWEETS (tweet_id varchar(255), channel_id varchar(255), UNIQUE(tweet_id, channel_id))')
            await db.execute('CREATE TABLE IF NOT EXISTS PHOTO_HASHES (user_id varchar(255), album_name varchar(255), photo_hash varchar(255), photo_path varchar(255), UNIQUE(user_id, album_name, photo_hash))')
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


    async def get_photo_path(self, user_id, album_name, photo_hash):
        # Returns the path of a photo with this hash, or None if we haven't seen it
        async with aiosqlite.connect(self.dbpath) as db:
            async with db.execute(f"SELECT photo_path FROM PHOTO_HASHES WHERE user_id = '{user_id}' AND album_name = '{album_name}' AND photo_hash = '{photo_hash}'") as cursor:
                result = await cursor.fetchone()
                return result[0] if result else None


    async def add_photo(self, user_id, album_name, photo_hash, photo_path):
        async with aiosqlite.connect(self.dbpath) as db:
            await db.execute(f"DELETE FROM PHOTO_HASHES WHERE user_id = '{user_id}' AND album_name = '{album_name}' AND photo_hash = '{photo_hash}'")
            await db.execute(f"INSERT OR IGNORE INTO PHOTO_HASHES (user_id, album_name, photo_hash, photo_path) VALUES ('{user_id}', '{album_name}', '{photo_hash}', '{photo_path}')")
            await db.commit()
        return True


    async def photo_path_indexed(self, photo_path):
        async with aiosqlite.connect(self.dbpath) as db:
            async with db.execute(f"SELECT * FROM PHOTO_HASHES WHERE photo_path = '{photo_path}'") as cursor:
                return await cursor.fetchone() != None

