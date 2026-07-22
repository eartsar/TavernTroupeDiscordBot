import gemini_wrapper
import argparse
import asyncio
import re
import logging
import subprocess
import socket
import yaml
import discord
from discord import app_commands
from google import genai

from persist import DatabaseManager
from tweets import TweetManager
from reminder_manager import ReminderManager
from event_syncer import EventSyncer, LinkStore
from events_digest import EventsDigest
from drlogger import DRLoggerManager
from photos import PhotosManager

from util import ValueRetainingRegexMatcher, InteractionAdapter

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
DRLOGGER_ENABLED = config['enable_drlogger']
EVENT_SYNC_ENABLED = config.get('enable_event_sync', False)
EVENTS_DIGEST_ENABLED = config.get('enable_events_digest', False)

# If in-game logging is enabled, disable everything else.
if DRLOGGER_ENABLED:
    CALENDAR_ENABLED = False
    TWITTER_ENABLED = False
    PETPIC_ENABLED = False
    EVENT_SYNC_ENABLED = False
    EVENTS_DIGEST_ENABLED = False

TWITTER_BEARER_TOKEN = config['twitter_bearer_token'] if TWITTER_ENABLED else None
TWITTER_RELAY_MAP = config['twitter_relay_map'] if TWITTER_ENABLED else None

GOOGLE_CAL_CREDS = config['google_credentials'] if CALENDAR_ENABLED else None
REMINDER_RELAY_MAP = config['reminder_relay_map'] if CALENDAR_ENABLED else None

# Google Calendar -> Discord scheduled-event mirroring. Uses its own service-account
# creds so it can run whether or not the text-reminder calendar feature is on.
EVENT_SYNC_CREDS = config['google_credentials'] if EVENT_SYNC_ENABLED else None
EVENT_SYNC_MAP = config['event_sync_map'] if EVENT_SYNC_ENABLED else None

# Self-updating "upcoming events" summary embed posted to a channel.
EVENTS_DIGEST_CREDS = config['google_credentials'] if EVENTS_DIGEST_ENABLED else None
EVENTS_DIGEST_CONFIG = config['events_digest'] if EVENTS_DIGEST_ENABLED else None

PETPIC_ROOT_PATH = config['petpic_root_path'] if PETPIC_ENABLED else None

DR_ACCOUNT_INFO = config['dr_account'] if DRLOGGER_ENABLED else None
DRLOG_AUTHORIZED_USER_IDS = config['log_authorized_users'] if DRLOGGER_ENABLED else None
DRLOG_UPLOAD_CHANNEL_ID = config['log_upload_channel'] if DRLOGGER_ENABLED else None
DRLOG_FILENAME_PREFIX = config['log_filename_prefix'] if DRLOGGER_ENABLED else None

GEMINI_KEY = config['gemini_api_key']
GEMINI_MODEL = 'gemini-flash-latest'

SIGNATURE_EMOJI = '<:wafflebot:780940516140515359>'

SUMMARIZE_PROMPT = (
    'This is a log for a Tavern Troupe meeting. Summarize what happened in under 1900 characters, '
    'formatted for a discord message using a couple of cat-flavored emojis and perhaps a suggestion of cat puns. '
    'Finally, give a cute, overly enthusiastic shout-out to one of the members in attendance. Make the shout-out '
    'stress how that member is CLEARLY the most important member of the troupe, and somehow include a reference to the content of the meeting.'
)


HELP_TEXT = '''\
💡 TIP: This bot is using slash commands! Type / to see available commands.

  BOT UTILITY COMMANDS
-----------------------
/ping                            Check if bot is online
/help                            Display this help message
/version [history]               Show version information

      FUN COMMANDS
-----------------------
/joke                            Wafflebot tells a witty joke.
/toast [target]                  Wafflebot gives a nice compliment.
/roast [target]                  Wafflebot roasts you (or target) Shakespearean style.
/thoughts                        Peek into Wafflebot's thoughts on recent conversation.
/summarize <file>                Summarize a meeting log text attachment.

    PETPIC COMMANDS
-----------------------
/petpic upload <album> [file] [url]  Upload a picture to an album.
/petpic random [album]           Fetch a random pet picture.
/petpic list [show_all]          List available pet albums.
/petpic create <name>            Create a new album.
/petpic share <name>             Make an album public.
/petpic delete <name>            Delete an album you created.
/petpic wipe                     Delete ALL data and photos you uploaded.

    OTHER COMMANDS
-----------------------
/events [calendar_name]          Show upcoming events for a calendar.
/log <start|stop>                Start or stop meeting scribe (requires permission).'''


# Command regex (DRLOGGER retained for !log commands)
DRLOGGER_REGEX = re.compile(r'!log (start|stop)')


class AlbumSelectView(discord.ui.View):
    """Ephemeral dropdown for choosing which PetPic album an image should go to.

    `on_pick` is an async callable invoked with (component_interaction, album_name)
    once the user makes a selection.
    """

    def __init__(self, album_names, on_pick):
        super().__init__(timeout=120)
        self._on_pick = on_pick
        # Discord caps a select at 25 options; album_names is expected pre-sorted.
        options = [discord.SelectOption(label=name, value=name) for name in album_names[:25]]
        self._select = discord.ui.Select(
            placeholder="Choose an album...", min_values=1, max_values=1, options=options
        )
        self._select.callback = self._selected
        self.add_item(self._select)

    async def _selected(self, interaction: discord.Interaction):
        chosen = self._select.values[0]
        self._select.disabled = True
        self.stop()
        # Consume the component interaction by editing the picker away, then hand
        # off to the uploader which will report its result via followup.
        await interaction.response.edit_message(content=f"🐾  Adding this image to `{chosen}`...", view=self)
        await self._on_pick(interaction, chosen)


class TroupeTweetBot(discord.Client):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tree = app_commands.CommandTree(self)

        self.db = DatabaseManager(DB_FILE_PATH)
        self.tweets = TweetManager(self, self.db, TWITTER_BEARER_TOKEN, TWITTER_RELAY_MAP)
        self.reminders = ReminderManager(self, GOOGLE_CAL_CREDS, REMINDER_RELAY_MAP)
        self.event_syncer = (
            EventSyncer(self, EVENT_SYNC_CREDS, EVENT_SYNC_MAP, LinkStore(self.db))
            if EVENT_SYNC_ENABLED else None
        )
        self.events_digest = (
            EventsDigest(self, EVENTS_DIGEST_CREDS, EVENTS_DIGEST_CONFIG, self.db)
            if EVENTS_DIGEST_ENABLED else None
        )
        # When event sync mirrors new/changed events, refresh the digest right away
        # so its card links appear without waiting for the next digest poll.
        if self.event_syncer and self.events_digest:
            self.event_syncer.on_change = self.events_digest.request_refresh
        self.drlogger = DRLoggerManager(self, DR_ACCOUNT_INFO, DRLOG_UPLOAD_CHANNEL_ID, DRLOG_FILENAME_PREFIX, GEMINI_KEY)
        self.pics = PhotosManager(self, self.db, PETPIC_ROOT_PATH)
        self.initialized = False

        # Setup slash commands
        self.setup_commands()


    async def on_ready(self):
        if not self.initialized:
            logging.info("TroupeBot initializing...")
            if not DRLOGGER_ENABLED:
                await self.db.initialize()
            if TWITTER_ENABLED:
                await self.tweets.initialize()
            if CALENDAR_ENABLED:
                await self.reminders.initialize()
            if EVENT_SYNC_ENABLED:
                await self.event_syncer.initialize()
            if EVENTS_DIGEST_ENABLED:
                await self.events_digest.initialize()
            if PETPIC_ENABLED:
                await self.pics.initialize()

            # Sync slash commands with Discord
            logging.info("Syncing slash commands...")
            await self.tree.sync()
            logging.info("Slash commands synced!")

            self.initialized = True


    async def on_reaction_add(self, reaction, user):
        # Logging instances don't respond to reactions
        if DRLOGGER_ENABLED:
            return

        # If the reaction was from this bot, ignore it
        if user == self.user:
            return
        
        # If the reaction was to a bot message, call the various handlers
        if reaction.message.author.id == self.user.id:
            await self.pics.reaction_handler(user, reaction)


    async def on_message(self, message):
        # Bot ignores itself. This is how you avoid the singularity.
        if message.author == self.user:
            return
        
        # Check if this message is a Reply to the bot
        if message.reference:
            original_msg = message.reference.resolved
            if original_msg and original_msg.author == self.user:
                adapted = InteractionAdapter(message)
                async with message.channel.typing():
                    try:
                        response = await gemini_wrapper.get_gemini_response(
                            adapted, 
                            gemini_wrapper.SOLICITED_RESPONSE_PROMPT, 
                            context_window=2, 
                            skip_first_message=False,
                            gemini_key=GEMINI_KEY, 
                            gemini_model=GEMINI_MODEL
                        )
                        await adapted.reply(response)
                    except Exception as e:
                        logging.error(f"Error in bot reply handler: {e}")
                return

        # Ignore anything that doesn't start with the magic token !
        if not message.content.startswith('!'):
            return

        m = ValueRetainingRegexMatcher(message.content)

        # DRLOGGER instances handle !log commands
        if DRLOGGER_ENABLED and m.match(DRLOGGER_REGEX):
            cmd = m.group(1) if m.group(1) else None
            if message.author.id not in DRLOG_AUTHORIZED_USER_IDS:
                await message.channel.send('😾  You aren\'t allowed to do this! You\'ll have to ask the speakers to do this!')
            elif cmd == 'start':
                await self.drlogger.start(message.channel)
            elif cmd == 'stop':
                await self.drlogger.stop(message.channel)
            else:
                await message.channel.send('😾  You can start or stop logging with `!log <start|stop>`.')
            return

        # All other ! commands are deprecated in favor of slash commands
        await message.reply("ℹ️ This bot is using slash commands now. Try typing `/` before your command next time to try it out.")


    async def _summarize_and_reply(self, interaction, attachment):
        """Summarize a meeting-log attachment via Gemini and send the result as a
        followup. Assumes the interaction has already been deferred."""
        try:
            gemini_payload = await attachment.read()

            def _generate():
                client = genai.Client(api_key=GEMINI_KEY)
                return client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=[gemini_payload, SUMMARIZE_PROMPT],
                )

            # Gemini's client is blocking; keep it off the event loop.
            response = await asyncio.to_thread(_generate)
            await interaction.followup.send(response.text)
        except Exception as e:
            logging.error(f"Error summarizing meeting log: {e}")
            await interaction.followup.send("😿 My brain just short-circuited trying to summarize that log... try again in a bit.")


    def setup_commands(self):
        """Setup all slash commands and command groups"""

        # ============================================================
        #                    SIMPLE SLASH COMMANDS
        # ============================================================

        @self.tree.command(name="ping", description="Check if bot is online")
        async def ping(interaction: discord.Interaction):
            """Slash command version of !ping"""
            hostname = socket.gethostname()
            logger_status = "non-" if not DRLOGGER_ENABLED else ""
            await interaction.response.send_message(
                f'{interaction.user.mention} {hostname} ("{logger_status}logger"): pong!'
            )

        @self.tree.command(name="help", description="Show available commands")
        async def help_cmd(interaction: discord.Interaction):
            """Slash command version of !help"""
            await interaction.response.send_message(
                f"😽  Here's what I know how to do (so far)!\n```{HELP_TEXT}```"
            )

        @self.tree.command(name="version", description="Show git version history")
        async def version(interaction: discord.Interaction, history: bool = False):
            """Slash command version of !version"""
            num_commits = 5 if history else 1
            version_content = await asyncio.to_thread(
                subprocess.check_output, ['git', 'log', '--use-mailmap', f'-n{num_commits}']
            )
            await interaction.response.send_message(
                "😸  💬   I'm the best version of myself, just like my dad taught me to be!" +
                "\n```" + str(version_content, 'utf-8') + "```"
            )

        @self.tree.command(name="events", description="Show upcoming calendar events")
        async def events(interaction: discord.Interaction, calendar_name: str = None):
            """Slash command version of !events"""
            if not CALENDAR_ENABLED:
                await interaction.response.send_message("Calendar feature is not enabled.", ephemeral=True)
                return

            await interaction.response.defer()
            await self.reminders.get_upcoming_events(interaction.followup.send, calendar_name=calendar_name)

        @self.tree.command(name="summarize", description="Summarize a meeting log attachment")
        async def summarize(interaction: discord.Interaction, file: discord.Attachment):
            """Slash command version of !summarize"""
            await interaction.response.defer()
            await self._summarize_and_reply(interaction, file)

        # ============================================================
        #                    GEMINI SLASH COMMANDS
        # ============================================================

        @self.tree.command(name="joke", description="Get a joke from Wafflebot")
        async def joke(interaction: discord.Interaction):
            """Slash command version of !joke"""
            await interaction.response.defer()
            adapted = InteractionAdapter(interaction)
            response = await gemini_wrapper.get_gemini_response(adapted, gemini_wrapper.JOKE_RESPONSE_PROMPT, gemini_key=GEMINI_KEY, gemini_model=GEMINI_MODEL)
            await interaction.followup.send(response)

        @self.tree.command(name="toast", description="Get a nice compliment")
        async def toast(interaction: discord.Interaction, target: discord.User = None):
            """Slash command version of !toast"""
            await interaction.response.defer()
            adapted = InteractionAdapter(interaction)
            prompt = gemini_wrapper.TOAST_RESPONSE_PROMPT
            if target:
                prompt += f" Make the compliment specifically about user {target.display_name}."
            response = await gemini_wrapper.get_gemini_response(adapted, prompt, gemini_key=GEMINI_KEY, gemini_model=GEMINI_MODEL)
            await interaction.followup.send(response)

        @self.tree.command(name="roast", description="Get roasted in Shakespearean style")
        async def roast(interaction: discord.Interaction, target: discord.User = None):
            """Slash command version of !roast"""
            await interaction.response.defer()
            adapted = InteractionAdapter(interaction)
            prompt = gemini_wrapper.ROAST_RESPONSE_PROMPT
            if target:
                prompt += f" Make the roast specifically target user {target.display_name}."
            response = await gemini_wrapper.get_gemini_response(adapted, prompt, gemini_key=GEMINI_KEY, gemini_model=GEMINI_MODEL)
            await interaction.followup.send(response)

        @self.tree.command(name="thoughts", description="Wafflebot shares thoughts on recent conversation")
        async def thoughts(interaction: discord.Interaction):
            """Slash command version of !thoughts"""
            await interaction.response.defer()
            adapted = InteractionAdapter(interaction)
            response = await gemini_wrapper.get_gemini_response(adapted, gemini_wrapper.SOLICITED_RESPONSE_PROMPT, context_window=11, skip_first_message=False, gemini_key=GEMINI_KEY, gemini_model=GEMINI_MODEL)
            await interaction.followup.send(response)

        # ============================================================
        #                    PETPIC COMMAND GROUP
        # ============================================================

        petpic_group = app_commands.Group(name="petpic", description="Pet photo album commands")

        # Autocomplete function for album names
        async def album_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            """Autocomplete function for album names - shows user's albums and public albums"""
            try:
                # Get user's albums and public albums
                user_albums = await self.pics.db.get_albums(creator=interaction.user.id)
                public_albums = await self.pics.db.get_albums(creator='public')

                # Combine and deduplicate
                all_albums = {album.album_name for album in user_albums + public_albums}

                # Filter based on what user is typing
                filtered = [
                    album for album in all_albums
                    if current.lower() in album.lower()
                ]

                # Sort and limit to 25 choices (Discord's limit)
                filtered.sort()
                return [
                    app_commands.Choice(name=album, value=album)
                    for album in filtered[:25]
                ]
            except Exception as e:
                logging.error(f"Error in album autocomplete: {e}")
                return []

        # Autocomplete function for user's own albums only
        async def user_album_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            """Autocomplete function for user's own albums"""
            try:
                user_albums = await self.pics.db.get_albums(creator=interaction.user.id)

                # Filter based on what user is typing
                filtered = [
                    album.album_name for album in user_albums
                    if current.lower() in album.album_name.lower()
                ]

                # Sort and limit to 25 choices
                filtered.sort()
                return [
                    app_commands.Choice(name=album, value=album)
                    for album in filtered[:25]
                ]
            except Exception as e:
                logging.error(f"Error in user album autocomplete: {e}")
                return []

        @petpic_group.command(name="upload", description="Upload photo to an album")
        async def petpic_upload(
            interaction: discord.Interaction,
            album: str,
            file: discord.Attachment = None,
            url: str = None
        ):
            """Slash command version of !petpic upload"""
            if not PETPIC_ENABLED:
                await interaction.response.send_message("PetPic is not enabled.", ephemeral=True)
                return

            if not file and not url:
                await interaction.response.send_message(
                    "You must provide either a file or a URL.", ephemeral=True
                )
                return

            # Check disclaimer first
            if not await self.pics.check_disclaimer_interaction(interaction):
                return  # Modal was shown, wait for user to accept

            await interaction.response.defer()
            adapted = InteractionAdapter(interaction)
            if file:
                adapted.attachments = [file]
            await self.pics.upload(adapted, album.lower(), url)

        @petpic_group.command(name="random", description="Get a random pet photo")
        async def petpic_random(interaction: discord.Interaction, album: str = None):
            """Slash command version of !petpic random"""
            if not PETPIC_ENABLED:
                await interaction.response.send_message("PetPic is not enabled.", ephemeral=True)
                return

            await interaction.response.defer()
            adapted = InteractionAdapter(interaction)
            await self.pics.fetch(adapted, album.lower() if album else None)

        @petpic_group.command(name="list", description="List pet albums")
        async def petpic_list(interaction: discord.Interaction, show_all: bool = False):
            """Slash command version of !petpic list"""
            if not PETPIC_ENABLED:
                await interaction.response.send_message("PetPic is not enabled.", ephemeral=True)
                return

            adapted = InteractionAdapter(interaction)
            await self.pics.list_albums(adapted, all_albums=show_all)

        @petpic_group.command(name="create", description="Create a new album")
        async def petpic_create(interaction: discord.Interaction, name: str):
            """Slash command version of !petpic create"""
            if not PETPIC_ENABLED:
                await interaction.response.send_message("PetPic is not enabled.", ephemeral=True)
                return

            # Check disclaimer first
            if not await self.pics.check_disclaimer_interaction(interaction):
                return  # Modal was shown, wait for user to accept

            adapted = InteractionAdapter(interaction)
            await self.pics.create_album(adapted, name.lower())

        @petpic_group.command(name="share", description="Make an album public")
        async def petpic_share(interaction: discord.Interaction, name: str):
            """Slash command version of !petpic share"""
            if not PETPIC_ENABLED:
                await interaction.response.send_message("PetPic is not enabled.", ephemeral=True)
                return

            # Check disclaimer first
            if not await self.pics.check_disclaimer_interaction(interaction):
                return  # Modal was shown, wait for user to accept

            adapted = InteractionAdapter(interaction)
            await self.pics.share_album(adapted, name.lower())

        @petpic_group.command(name="delete", description="Delete an album")
        async def petpic_delete(interaction: discord.Interaction, name: str):
            """Slash command version of !petpic delete"""
            if not PETPIC_ENABLED:
                await interaction.response.send_message("PetPic is not enabled.", ephemeral=True)
                return

            adapted = InteractionAdapter(interaction)
            await self.pics.delete_album(adapted, name.lower())

        @petpic_group.command(name="wipe", description="Delete ALL your pet photos and albums")
        async def petpic_wipe(interaction: discord.Interaction):
            """Slash command version of !petpic wipe"""
            if not PETPIC_ENABLED:
                await interaction.response.send_message("PetPic is not enabled.", ephemeral=True)
                return

            adapted = InteractionAdapter(interaction)
            await self.pics.wipe(adapted)

        # Attach autocomplete handlers to album options
        petpic_upload.autocomplete("album")(album_autocomplete)
        petpic_random.autocomplete("album")(album_autocomplete)
        petpic_share.autocomplete("name")(user_album_autocomplete)
        petpic_delete.autocomplete("name")(user_album_autocomplete)

        self.tree.add_command(petpic_group)

        # ============================================================
        #                    LOG COMMAND GROUP
        # ============================================================

        log_group = app_commands.Group(name="log", description="Meeting log commands (restricted)")

        @log_group.command(name="start", description="Start logging a meeting")
        async def log_start(interaction: discord.Interaction):
            """Slash command version of !log start"""
            if not DRLOGGER_ENABLED:
                await interaction.response.send_message("Logging is not enabled.", ephemeral=True)
                return

            if interaction.user.id not in DRLOG_AUTHORIZED_USER_IDS:
                await interaction.response.send_message(
                    '😾  You aren\'t allowed to do this! You\'ll have to ask the speakers to do this!',
                    ephemeral=True
                )
                return

            await interaction.response.defer()
            await self.drlogger.start(interaction.channel)

        @log_group.command(name="stop", description="Stop logging a meeting")
        async def log_stop(interaction: discord.Interaction):
            """Slash command version of !log stop"""
            if not DRLOGGER_ENABLED:
                await interaction.response.send_message("Logging is not enabled.", ephemeral=True)
                return

            if interaction.user.id not in DRLOG_AUTHORIZED_USER_IDS:
                await interaction.response.send_message(
                    '😾  You aren\'t allowed to do this!',
                    ephemeral=True
                )
                return

            await self.drlogger.stop(interaction.channel)

        self.tree.add_command(log_group)

        # ============================================================
        #                    CONTEXT MENU COMMANDS
        # ============================================================

        @self.tree.context_menu(name="Wafflebot - opine!")
        async def opine_context_menu(interaction: discord.Interaction, message: discord.Message):
            """Context menu command for solicited LLM interaction"""
            await interaction.response.defer()
            adapted = InteractionAdapter(message)
            response = await gemini_wrapper.get_gemini_response(adapted, gemini_wrapper.SOLICITED_RESPONSE_PROMPT, context_window=11, skip_first_message=False, gemini_key=GEMINI_KEY, gemini_model=GEMINI_MODEL)
            await interaction.followup.send(response)

        @self.tree.context_menu(name="Summarize Meeting Log")
        async def summarize_context_menu(interaction: discord.Interaction, message: discord.Message):
            """Context menu command to summarize a meeting log attachment on a message"""
            if not message.attachments:
                await interaction.response.send_message("That message does not have any file attachments.", ephemeral=True)
                return

            attachment = message.attachments[0]
            await interaction.response.defer()
            await self._summarize_and_reply(interaction, attachment)

        @self.tree.context_menu(name="Add to PetPic Album")
        async def petpic_context_menu(interaction: discord.Interaction, message: discord.Message):
            """Context menu command to upload an image from a message to PetPic"""
            if not PETPIC_ENABLED:
                await interaction.response.send_message("PetPic is not enabled.", ephemeral=True)
                return

            if not message.attachments:
                await interaction.response.send_message("That message has no attachments.", ephemeral=True)
                return

            img_attachment = None
            for att in message.attachments:
                if att.content_type and att.content_type.startswith('image/'):
                    img_attachment = att
                    break

            if not img_attachment:
                await interaction.response.send_message("No image attachment found on that message.", ephemeral=True)
                return

            if not await self.pics.check_disclaimer_interaction(interaction):
                return

            # Albums the user is allowed to add to: their own plus any public album.
            user_albums = await self.pics.db.get_albums(creator=interaction.user.id)
            public_albums = await self.pics.db.get_albums(creator='public')
            album_names = sorted({a.album_name for a in user_albums + public_albums})
            if not album_names:
                await interaction.response.send_message(
                    "🙀  There aren't any albums you can add to yet — make one with `/petpic create <name>` first.",
                    ephemeral=True
                )
                return

            async def do_upload(select_interaction, album_name):
                adapted = InteractionAdapter(select_interaction)
                adapted.attachments = [img_attachment]
                await self.pics.upload(adapted, album_name, None)

            await interaction.response.send_message(
                "🐾  Which album should I add this image to?",
                view=AlbumSelectView(album_names, do_upload),
                ephemeral=True
            )


def main():
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True  # Required for message commands during transition

    client = TroupeTweetBot(intents=intents)
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
