import aiosqlite
import logging


class DatabaseManager():
    def __init__(self, sqlite3_file):
        self.dbpath = sqlite3_file
    

    async def initialize(self):
        async with aiosqlite.connect(self.dbpath) as db:
            logging.info("Connecting to and preparing SQLITE database...")
            await db.execute('CREATE TABLE IF NOT EXISTS CACHED_TWEETS (tweet_id varchar(255), channel_id varchar(255), UNIQUE(tweet_id, channel_id))')
            await db.execute('CREATE TABLE IF NOT EXISTS ALBUMS (album_name varchar(255), creator varchar(255), UNIQUE(album_name))')
            await db.execute('CREATE TABLE IF NOT EXISTS PHOTOS (photo_name varchar(255), album_name varchar(255), uploader varchar(255), UNIQUE(photo_name, album_name))')
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


    async def create_album(self, album_name, creator):
        async with aiosqlite.connect(self.dbpath) as db:
            async with db.execute(f"INSERT INTO ALBUMS (album_name, creator) VALUES ('{album_name}', '{creator}')") as cursor:
                await db.commit()
                return cursor.rowcount == 1


    async def delete_album(self, album_name):
        async with aiosqlite.connect(self.dbpath) as db:
            album_success = False
            photos_success = False

            async with db.execute("DELETE FROM ALBUMS WHERE album_name = ?", (album_name,)) as cursor:
                await db.commit()
                album_success = cursor.rowcount == 1

            async with db.execute("DELETE FROM PHOTOS WHERE album_name = ?", (album_name,)) as cursor:
                await db.commit()
                photos_success = cursor.rowcount > 0

            return album_success and photos_success



    async def wipe_user_albums(self, creator):
        async with aiosqlite.connect(self.dbpath) as db:
            async with db.execute("DELETE FROM ALBUMS WHERE creator = ?", (creator,)) as cursor:
                await db.commit()
                return cursor.rowcount


    async def get_albums(self, album_name=None, creator=None):
        async with aiosqlite.connect(self.dbpath) as db:
            query = "SELECT * FROM ALBUMS"
            
            criteria = []
            args = []
            if creator:
                criteria.append('creator = ?')
                args.append(creator)
            if album_name:
                criteria.append('album_name = ?')
                args.append(album_name)

            query += (' WHERE ' + ' AND '.join(criteria)) if criteria else ''
            async with db.execute(query, tuple(args)) as cursor:
                return [_[0] for _ in await cursor.fetchall()]


    async def album_exists(self, album_name, creator=None):
        return len(await self.get_albums(album_name=album_name, creator=creator)) > 0


    async def user_owns_album(self, album_name, user_id):
        return await self.album_exists(album_name, creator=user_id)


    async def is_album_public(self, album_name):
        return await self.user_owns_album(album_name, 'public')


    async def make_album_public(self, album_name):
        async with aiosqlite.connect(self.dbpath) as db:
            async with db.execute("UPDATE ALBUMS SET creator = 'public' WHERE album_name = ?", (album_name,)) as cursor:
                await db.commit()
                return cursor.rowcount == 1


    async def get_album_metadata(self, album_name):
        return {'public': await self.is_album_public(album_name), 'count': len(await self.get_photos(album_name=album_name))}


    async def add_photo(self, photo_name, album_name, uploader, silently=False):
        async with aiosqlite.connect(self.dbpath) as db:
            async with db.execute(f"\
                INSERT {'OR IGNORE' if silently else ''} INTO PHOTOS \
                (photo_name, album_name, uploader) VALUES (?, ?, ?)", (photo_name, album_name, uploader)) as cursor:
                await db.commit()
                return cursor.rowcount == 1


    async def wipe_user_photos(self, uploader):
        async with aiosqlite.connect(self.dbpath) as db:
            async with db.execute("DELETE FROM PHOTOS WHERE uploader = ?", (uploader,)) as cursor:
                await db.commit()
                return cursor.rowcount


    async def get_photos(self, uploader=None, album_name=None):
        async with aiosqlite.connect(self.dbpath) as db:
            query = 'SELECT * FROM PHOTOS'
            
            criteria = []
            args = []
            if uploader:
                criteria.append('uploader = ?')
                args.append(uploader)
            if album_name:  
                criteria.append('album_name = ?')
                args.append(album_name)

            query += (' WHERE ' + ' AND '.join(criteria)) if criteria else ''

            async with db.execute(query, tuple(args)) as cursor:
                return [_[0] for _ in await cursor.fetchall()]


    async def delete_photos(self, filenames):
        async with aiosqlite.connect(self.dbpath) as db:
            async with db.execute(f"DELETE FROM PHOTOS WHERE photo_name IN ({','.join(['?']*len(filenames))})", tuple(filenames)) as cursor:
                await db.commit()
                return cursor.rowcount


    
