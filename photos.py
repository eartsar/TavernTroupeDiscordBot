import sys
import asyncio
import logging
import shutil
import os
import random
import glob
import zipfile
import tempfile
import time
import hashlib
import pathlib
import imghdr
import dataclasses
import subprocess
import math

import discord
import aiofiles
import gdown
import requests


@dataclasses.dataclass
class Photo:
    photo_name: str
    album_name: str
    uploader: int
    freq: int


@dataclasses.dataclass
class Album:
    album_name: str
    creator: int


DISCLAIMER_MESSAGE = '''\
ℹ️ BEFORE YOU USE THIS FEATURE, YOU MUST READ THE FOLLOWING DISCLAIMER ℹ️

```Hi trouper. I'm not a lawyer or anything, but I need to be *really clear* 
about a few things before you do this. Also, sorry for constantly popping
this up - I do it whenever the underlying code has changed. So if you're
seeing this - it's because I'm a new, shinier version of myself!

1 - You are uploading a file to a personal server. Don't do this unless 
    you're comfortable with that. This should be obvious, I said it.

2 - While I SHOULD have working functionality to remove photos, I can't
    PROMISE the capability in perpetuity.

3 - You're giving consent for other people in this Discord server - or any
    other Discord server I might be installed on - to potentially view these
    photos through the current implemented features that I support.

4 - I will do my best to be respectful with your data.

4 - You will NOT give me anything even REMOTELY inappropriate. Failure to
    adhere to this can result in me being turned off, or worse.

5 - By clicking the 🆗 emoji below, you understand the risks you're taking
    in using this feature. You recognize that anything that happens after
    submitting data to me is out of your hands. You also recognize that
    I've given my word, and if I fail to uphold my end of the bargain,
    you're within your right to be pissed off and seek revenge.

If you think this disclaimer missed anything that should be made clear, or
if you have any questions or comments about what I do with your data, please
do not hesitate to reach out to Discord user eartsar#3210 - my creator.```'''

ACCEPTABLE_FILETYPES = ('jpg', 'jpeg', 'gif', 'png', 'tiff')

MAX_PHOTO_SIZE = 8388608


def requires_disclaimer(fn):
    '''
    Decorator that forces the user to accept the disclaimer. This acceptance is
    cached, and cleared on every code deploy.

    @requires_disclaimer
    async def some_function_where_consent_is_important(self, message, ...):
        ...
    '''
    from functools import wraps
    @wraps(fn)
    async def wrapper(self, message, *args, **kwargs):
        user_id = message.author.id
        self.sent_command_cache[user_id] = message
        if user_id not in self.accepted_cache:
            pending = None
            for key in self.pending_cache:
                if self.pending_cache[key] == user_id:
                    # send them a reminder to the open message
                    return await message.author.send(f'You have to accept the disclaimer here: {key.jump_url}')
            # send them a disclaimer
            disclaimer_msg = await message.author.send(DISCLAIMER_MESSAGE)
            await disclaimer_msg.add_reaction('🆗')
            await disclaimer_msg.add_reaction('🛑')
            self.pending_cache[disclaimer_msg] = user_id
            return

        # They accepted, all good to go
        return await fn(self, message, *args, **kwargs)
    return wrapper


class PhotosManager():
    def __init__(self, bot, db, photos_root_path):
        self.bot = bot
        self.db = db
        self.photos_root_path = photos_root_path
        
        # set of user_ids who accepted the disclaimer
        self.accepted_cache = set()
        
        # dict from message --> user_id
        # the key is the message of the command that triggered the disclaimer
        self.pending_cache = {}
        
        # dict of user_id --> string
        # the last command entered that caused the disclaimer to pop up
        self.sent_command_cache = {}


    async def initialize(self):
        '''
        Indexes all unindexed photos.
        '''
        logging.info("Initializing photos manager...")
        await self.update_index()
        logging.info('Done.')
        return


    async def update_index(self):
        logging.info("Updating photo hash index...")

        all_photo_paths = set([os.path.basename(_) for _ in glob.glob(os.path.join(self.photos_root_path, '*'))])
        all_indexed_photos = set([_.photo_name for _ in await self.db.get_photos()])

        to_remove_from_disk = all_photo_paths - all_indexed_photos
        to_prune_from_db = all_indexed_photos - all_photo_paths

        def delete_files(paths):
            count = 0
            for path in paths:
                try:
                    os.remove(os.path.join(self.photos_root_path, path))
                    count += 1
                except OSError:
                    pass
            return count

        # Purge photos uploaded by user from disk
        num_files_deleted = await asyncio.get_event_loop().run_in_executor(None, delete_files, to_remove_from_disk)
        logging.info(f'Removed {num_files_deleted} unindexed files from disk.')

        num_entries_purged = await self.db.delete_photos(to_prune_from_db)
        logging.info(f'Removed {num_entries_purged} lingering index entries from the DB.')


    async def reaction_handler(self, user, reaction):
        '''
        Hook that the main bot application calls on a reaction to see
        if this object should do anything about it
        '''
        # Ignore reactions to messages that are not disclaimer messages
        if reaction.message not in self.pending_cache:
            return
        
        # If the reaction is from the owner, and a valid option, interpet it. Otherwise, purge.
        if user.id == self.pending_cache[reaction.message] and reaction.count > 1:
            if reaction.emoji == '🆗':
                del self.pending_cache[reaction.message]
                self.accepted_cache.add(user.id)
                await user.send('👍  You may now use the feature.')
                return await self.bot.on_message(self.sent_command_cache[user.id])
            if reaction.emoji == '🛑':
                del self.pending_cache[reaction.message]
                return await user.send('Please note that accepting the disclaimer is a requirement for using the feature.')
        else:
            await reaction.remove(user)


    @requires_disclaimer
    async def create_album(self, message, album_name):
        '''
        Create an album by creating a dir on disk
        photo_root/{user_id}/{album_name}
        '''
        from sqlite3 import IntegrityError
        try:
            await self.db.create_album(album_name, message.author.id)
        except IntegrityError:
            logging.warning(f'{message.author.id} attempted to create album {album_name} which already exists.')
            return await message.channel.send(f'{message.author.mention} - There is already an album named `{album_name}`.')
        return await message.channel.send(f'{message.author.mention} - Created album `{album_name}`.')


    async def delete_album(self, message, album_name):
        '''
        Delete an album on disk and all its contents
        '''
        if not await self.db.user_owns_album(album_name, message.author.id):
            return await message.channel.send(f"{message.author.mention} - You don't have an album named `{album_name}`.")
        
        await self.db.delete_album(album_name)
        return await message.channel.send(f'{message.author.mention} - Deleted album `{album_name}`.')


    async def wipe(self, message):
        '''
        Delete all of a user's albums and contents
        '''
        paths = [os.path.join(self.photos_root_path, _.photo_name) for _ in await self.db.get_photos(uploader=message.author.id)]

        def delete_files(paths):
            count = 0
            for path in paths:
                try:
                    os.remove(path)
                    count += 1
                except OSError:
                    pass
            return count

        # Purge photos uploaded by user from disk
        num_files_deleted = await asyncio.get_event_loop().run_in_executor(None, delete_files, paths)
        logging.info(f"Wiping {str(message.author.id)}'s petpic data...")
        logging.info(f'  {num_files_deleted} files deleted from disk.')
        
        # Remove entities from the DB
        albums_to_remove = [_.album_name for _ in await self.db.get_albums(creator=message.author.id)]
        num_albums_removed = await self.db.wipe_user_albums(message.author.id)
        logging.info(f'  Albums removed from DB: {", ".join(sorted(albums_to_remove))}')
        
        num_photos_removed = await self.db.wipe_user_photos(message.author.id)
        logging.info(f'  {num_photos_removed} photos removed from DB.')

        return await message.channel.send(f'{message.author.mention} - All your uploaded photos and albums have been deleted.')


    @requires_disclaimer
    async def share_album(self, message, album_name):
        '''
        Share an album so that it can be used by everyone
        '''
        if not await self.db.album_exists(album_name):
            return await message.channel.send(f"{message.author.mention} - There is no album named `{album_name}`.")
        if not await self.db.user_owns_album(album_name, message.author.id):
            return await message.channel.send(f"{message.author.mention} - You don't own the album `{album_name}`.")

        await self.db.make_album_public(album_name) 
        return await message.channel.send(f'{message.author.mention} - Album {album_name} is now open to everyone.')


    async def list_albums(self, message, all_albums=False):
        '''
        Get a list of all albums for a user, or all albums in general
        '''
        albums = await self.db.get_albums(creator=(message.author.id if not all_albums else None))
        if not albums:
            if all_albums:
                return await message.channel.send(f"{message.author.mention} - there aren't any albums! Make the first one!")
            else:
                return await message.channel.send(f"{message.author.mention} - you don't have any albums!")
        
        albums = sorted(albums, key=lambda x: x.album_name)
        
        lines = []
        for album in albums:
            marker = "* " if album.creator == 'public' else "  "
            count = len(await self.db.get_photos(album_name=album.album_name))
            lines.append(f'{marker}{album.album_name} - {count} photos.')
        
        album_listing = '\n'.join(lines) + '\n\n* this is a public album'
        return await message.channel.send(f'{message.author.mention} - I found the following albums:```\n{album_listing}```')


    async def fetch(self, message, album_name):
        '''
        Fetches a random photo and posts it
        '''
        if album_name and not await self.db.album_exists(album_name):
            return await message.channel.send(f'{message.author.mention} - There is no album named `{album_name}`.')
        
        all_photos = await self.db.get_photos(album_name=album_name)
        if not all_photos:
            return await message.channel.send(f"I couldn't find any photos!")

        # Calculate weights. This heuristic adds one to the freq of all photos (to avoid zero), creates an inverse weight, and
        # then normalizes the weights for all photos.
        for photo in all_photos:
            photo.freq += 1

        weights = [1/photo.freq for photo in all_photos]
        total_weight = sum(weights)
        weights = [_/total_weight for _ in weights]

        # Bin the weights for display, and decimal shift them to make spark happy
        spark_weights = sorted([_ * 100000 for _ in weights])
        chunk = 100
        if len(spark_weights) > chunk:
            factor = math.ceil(len(spark_weights) / chunk)
            # spark_weights = spark_weights[:factor*chunk]
            bins = []
            for i in range(0, len(spark_weights), factor):
                bins.append(sum(spark_weights[i:i+factor])/factor)
            spark_weights = bins
        # stringify for subprocess
        spark_weights = [str(_) for _ in reversed(spark_weights)]
        cmd = 'spark ' + ' '.join(spark_weights)
        logging.info('petpic bias: ' + subprocess.check_output(cmd, shell=True).decode('utf-8'))

        random_photo = random.choices(all_photos, weights=weights)[0]
        random_photo_path = os.path.join(self.photos_root_path, random_photo.photo_name)
        with open(random_photo_path, 'rb') as f:
            ext = imghdr.what(random_photo_path)
            send_file = discord.File(f, filename=f.name + '.' + ext, spoiler=False)
            await message.channel.send(f"Here's a random photo from the album `{random_photo.album_name}`!", file=send_file)
        
        return await self.db.increment_photo_freq(random_photo)


    @requires_disclaimer
    async def upload(self, message, album_name, url):
        '''
        Adds photos to storage.

        The bot will first assume that the photo is an attachment.
        If a URL is supplied instead, it downloads that instead.
        '''
        if not album_name:
            return await message.channel.send(f'{message.author.mention} - You need to tell me which album to add to.')

        if not await self.db.album_exists(album_name):
            return await message.channel.send(f'{message.author.mention} - There is no album named `{album_name}`.')
        elif not await self.db.user_owns_album(album_name, message.author.id) and not await self.db.is_album_public(album_name):
            return await message.channel.send(f"{message.author.mention} - You don't have access to the album `{album_name}`.")
        elif not message.attachments and not url:
            return await message.channel.send(f'{message.author.mention} - You need to attach either a photo or supply a url to download from')

        # If the file is an attachment, validate it, and move it to the right spot
        if message.attachments:
            attachment = message.attachments[0]

            if attachment.size > MAX_PHOTO_SIZE:
                logging.warning(f'User {message.author.id} uploaded file via attachment - REJECTED: File exceeds maximum allowed size')
                return await message.channel.send(f'{message.author.mention} - Image files must be less than 8 Megabytes')

            # Uploaded file meets all requirements
            try:
                # Create a temporary space to download the photo, then do the proper placement
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_photo_path = os.path.join(temp_dir, attachment.filename)
                    await attachment.save(temp_photo_path)

                    if imghdr.what(temp_photo_path) not in ACCEPTABLE_FILETYPES:
                        logging.warning(f'User {message.author.id} uploaded file via attachment - REJECTED: Bad file type')
                        return await message.channel.send(f'{message.author.mention} - The attached file is not a valid photo or archive.')

                    await self._place_photo(temp_photo_path, message.author.id, album_name)
            except Exception:
                logging.exception(f'Exception thrown while saving file for {message.author.id} to album {album_name}')
                return await message.channel.send(f'{message.author.mention} - Something went wrong when downloading the file.')
        
        # If the URL was supplied, branch into custom logic to download the archive, and handle accordingly
        elif url:
            try:
                num_added = await asyncio.get_event_loop().run_in_executor(None, self.download_and_extract, url, message.author.id, album_name)
                await message.channel.send(f'{message.author.mention} - {str(num_added)} files were added to album `{album_name}`.')        
            except Exception:
                logging.exception(f'Exception thrown while downloading from url ({url}) supplied by {message.author.id}')
                return await message.channel.send(f"{message.author.mention} - Something went wrong with fetching the zip. \
Many cloud services have a landing page on publicly accessible files that I can't deal with yet. Right now I know how to download \
from **Google Drive** and **Dropbox**, but I'll try any link you give me!")

        return await message.add_reaction('✅')


    async def _place_photo(self, photo_path, user_id, album_name):
        '''
        Hash photo, move it, and register to DB
        '''
        
        # Generate a hash for the file, which is also the name on disk
        hashobj = hashlib.blake2b()
        with open(photo_path, 'rb') as photo_file:
            # This is basically instant for a file of small size
            hashobj.update(photo_file.read())

        photo_hash = hashobj.hexdigest()

        # Move the file to the proper location on disk
        new_photo_path = os.path.join(self.photos_root_path, photo_hash)
        overwrite = os.path.exists(new_photo_path)
        
        try:
            shutil.move(photo_path, new_photo_path)
        except Exception:
            logging.exception(f'Could not mv {photo_path} --> {new_photo_path}.')

        await self.db.add_photo(photo_hash, album_name, user_id, silently=True)
        logging.info(f"PhotoManager: user {user_id} {'overwrote' if overwrite else 'added'} photo {new_photo_path} to album {album_name}")

    
    def download_and_extract(self, url, user_id, album_name):
        '''
        NOT AN ASYNC METHOD - MUST RUN IN A THREAD
        '''
        method = 'direct'
        # https://www.dropbox.com/s/nrb3cf7z0k1ch3l/two_waffle_pics.zip?dl=0
        if 'drive.google.com/file/d/' in url:
            method = 'google'
            start = url.find('drive.google.com/file/d/')
            drive_id = url[start:].split('/')[3]
            url = f'https://drive.google.com/uc?id={drive_id}'
        elif 'dropbox.com' in url and '?dl=0' in url:
            start = url.find('?dl=0')
            url = url[:start] + '?dl=1' + url[start + 5:]

        logging.info(f'Downloading from url {url}')
        with tempfile.TemporaryDirectory() as temp_dir:
            download_path = os.path.join(temp_dir, 'temp.zip')
            
            if method == 'google':
                gdown.download(url, download_path, quiet=True)
            elif method == 'direct':
                r = requests.get(url, stream=True)
                with open(download_path, 'wb') as out_zip:
                    for chunk in r.iter_content(chunk_size=4096):
                        out_zip.write(chunk)

            logging.info(f'Extracting downloaded zip to {temp_dir}')
            with zipfile.ZipFile(download_path, "r") as zip_ref:
                zip_ref.extractall(temp_dir)

            os.remove(download_path)

            def is_ok(img):
                if os.path.basename(img)[0] == '.':
                    return False
                if os.path.isdir(img):
                    return False
                if os.path.getsize(img) > MAX_PHOTO_SIZE:
                    return False
                if imghdr.what(img) not in ACCEPTABLE_FILETYPES:
                    return False
                return True

            # Cut out files of that are too big, and hidden files, and non-image types
            sanitized_photo_paths = [_ for _ in filter(is_ok, glob.glob(os.path.join(temp_dir, '*')))]
            for temp_photo_path in sanitized_photo_paths:
                asyncio.run(self._place_photo(temp_photo_path, user_id, album_name))

        return len(sanitized_photo_paths)

