import asyncio
import hashlib
import logging
import aiosqlite
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import discord
from bs4 import BeautifulSoup
from dateutil import tz
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from requests.structures import CaseInsensitiveDict

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# All-day events carry only a date, which is local to the calendar rather than
# UTC. Anchor them to this zone so mirrored Discord events land on the right day.
LOCAL_TZ = tz.gettz('America/New_York')

SYNC_INTERVAL = 300            # seconds between sync passes
DEFAULT_HORIZON_DAYS = 30      # how far ahead to mirror events
MAX_EVENTS = 250               # per-pass fetch cap (see notes on paging)

# Discord field limits.
NAME_LIMIT = 100
DESCRIPTION_LIMIT = 1000
LOCATION_LIMIT = 100

Link = namedtuple('Link', 'discord_id signature')


def _strip_html(text):
    if not text:
        return ''
    if BeautifulSoup(text, 'html.parser').find():
        text = text.replace('<br>', '\n').replace('<wbr>', '').replace('&nbsp;', '')
        text = BeautifulSoup(text, 'lxml').text
    return text.strip()


# ---------------------------------------------------------------- persistence

class LinkStore:
    """Persistent (calendar_id, google_id) -> (discord_id, signature) mapping.

    This is the source of truth for which Discord event mirrors which Google
    event. It must outlive process restarts, or the syncer would re-create
    everything and orphan the previous Discord events.
    """

    def __init__(self, dbpath_or_manager='local_storage/troupebot.db'):
        if hasattr(dbpath_or_manager, 'dbpath'):
            self.dbpath = dbpath_or_manager.dbpath
            self.db_manager = dbpath_or_manager
        else:
            self.dbpath = dbpath_or_manager
            self.db_manager = None

    async def initialize(self):
        if self.db_manager:
            await self.db_manager.initialize()
        else:
            async with aiosqlite.connect(self.dbpath) as db:
                await db.execute(
                    """CREATE TABLE IF NOT EXISTS EVENT_LINKS (
                           calendar_id TEXT    NOT NULL,
                           google_id   TEXT    NOT NULL,
                           discord_id  INTEGER NOT NULL,
                           signature   TEXT    NOT NULL,
                           PRIMARY KEY (calendar_id, google_id)
                       )"""
                )
                await db.commit()

    async def get_links(self, calendar_id):
        if self.db_manager:
            rows = await self.db_manager.get_event_links(calendar_id)
        else:
            async with aiosqlite.connect(self.dbpath) as db:
                async with db.execute(
                    "SELECT google_id, discord_id, signature FROM EVENT_LINKS WHERE calendar_id = ?",
                    (calendar_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
        return {gid: Link(did, sig) for gid, did, sig in rows}

    async def upsert(self, calendar_id, google_id, discord_id, signature):
        if self.db_manager:
            await self.db_manager.upsert_event_link(calendar_id, google_id, discord_id, signature)
        else:
            async with aiosqlite.connect(self.dbpath) as db:
                await db.execute(
                    """INSERT INTO EVENT_LINKS (calendar_id, google_id, discord_id, signature)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(calendar_id, google_id)
                       DO UPDATE SET discord_id = excluded.discord_id,
                                     signature  = excluded.signature""",
                    (calendar_id, google_id, discord_id, signature),
                )
                await db.commit()

    async def delete(self, calendar_id, google_id):
        if self.db_manager:
            await self.db_manager.delete_event_link(calendar_id, google_id)
        else:
            async with aiosqlite.connect(self.dbpath) as db:
                await db.execute(
                    "DELETE FROM EVENT_LINKS WHERE calendar_id = ? AND google_id = ?",
                    (calendar_id, google_id),
                )
                await db.commit()


# ---------------------------------------------------------------- syncer

class EventSyncer:
    """Mirrors Google Calendar events into a guild's Discord scheduled events.

    sync_map: { label: {
        'calendar_id': '...@group.calendar.google.com',
        'guild_id': 123456789012345678,
        'horizon_days': 30,                      # optional
        'default_location': 'See description',   # optional; used when the
                                                 #   Google event has no location
    } }
    """

    def __init__(self, bot, google_creds, sync_map, store):
        self.bot = bot
        self.google_creds = google_creds
        self.sync_map = CaseInsensitiveDict(sync_map)
        self.store = store
        self.tasks = []
        # Optional no-arg callback invoked after any pass that created/updated/
        # deleted a Discord event (i.e. changed EVENT_LINKS). Lets the events
        # digest re-render its links promptly instead of waiting out its interval.
        self.on_change = None

    async def initialize(self):
        logging.info("Initializing Google Calendar -> Discord event sync...")
        await self.store.initialize()
        for label, cfg in self.sync_map.items():
            logging.info(f"\tSyncing '{label}' ({cfg['calendar_id']}) -> guild {cfg['guild_id']}")
            self.tasks.append(asyncio.create_task(self._sync_loop(label, cfg)))
        logging.info("Done.")

    def _build_service(self):
        creds = service_account.Credentials.from_service_account_file(
            self.google_creds, scopes=SCOPES
        )
        return build('calendar', 'v3', credentials=creds, cache_discovery=False)

    async def _sync_loop(self, label, cfg):
        service = None
        while True:
            try:
                if service is None:
                    service = await asyncio.to_thread(self._build_service)
                guild = self.bot.get_guild(int(cfg['guild_id']))
                if guild is None:
                    logging.warning(f"\tGuild {cfg['guild_id']} for '{label}' not available; skipping pass.")
                else:
                    changed = await self._sync_once(service, guild, cfg)
                    if changed and self.on_change is not None:
                        try:
                            self.on_change()
                        except Exception:
                            logging.exception("event sync on_change callback failed")
            except Exception:
                logging.exception(f"Error syncing calendar '{label}'")
            await asyncio.sleep(SYNC_INTERVAL)

    async def _sync_once(self, service, guild, cfg):
        calendar_id = cfg['calendar_id']
        default_location = cfg.get('default_location', 'See event description')
        horizon_days = cfg.get('horizon_days', DEFAULT_HORIZON_DAYS)

        now = datetime.now(timezone.utc)
        window_end = now + timedelta(days=horizon_days)

        # What Google currently has in our forward window (cancelled dropped).
        windowed = await self._fetch_events(service, calendar_id, now, window_end)
        present = {ev['id']: ev for ev in windowed if ev.get('status') != 'cancelled'}

        links = await self.store.get_links(calendar_id)

        # A tracked event that isn't in the window might have moved out past the
        # horizon or already started -- not deleted. Confirm each individually so
        # we only tear down events Google has genuinely dropped.
        for gid in list(links):
            if gid in present:
                continue
            ev = await self._get_event(service, calendar_id, gid)
            if ev is not None:
                present[gid] = ev

        discord_events = {se.id: se for se in await guild.fetch_scheduled_events()}

        # Index our OWN scheduled events that aren't currently tracked, keyed by
        # identity. This lets a reconcile whose DB link was lost (e.g. a crash
        # between create and the link write) re-adopt the real event instead of
        # creating a duplicate -- and clean up any duplicates already made. Only
        # our bot's events are ever considered, so human-created events are safe.
        linked_ids = {link.discord_id for link in links.values()}
        bot_id = self.bot.user.id if self.bot.user else None
        orphans = {}
        if bot_id is not None:
            for se in discord_events.values():
                if se.id in linked_ids or se.creator_id != bot_id:
                    continue
                orphans.setdefault(self._identity(se.name, se.start_time), []).append(se)

        changed = False

        # Create / update everything Google still knows about.
        for gid, ev in present.items():
            if await self._reconcile(guild, calendar_id, gid, ev, links, discord_events,
                                     orphans, default_location, now):
                changed = True

        # Delete Discord events whose Google source is gone. Note this only ever
        # iterates our own store, so it can't touch unrelated Discord events.
        for gid, link in links.items():
            if gid in present:
                continue
            se = discord_events.get(link.discord_id)
            if se is not None and se.status is discord.EventStatus.scheduled:
                try:
                    await se.delete(reason='Removed from Google Calendar')
                    logging.info(f"Deleted Discord event for {gid}")
                except discord.DiscordException:
                    logging.exception(f"Failed to delete Discord event {link.discord_id}")
            await self.store.delete(calendar_id, gid)
            changed = True

        return changed

    async def _reconcile(self, guild, calendar_id, gid, ev, links, discord_events,
                         orphans, default_location, now):
        """Create or update the Discord event for one Google event. Returns True if
        anything changed (create/update/duplicate cleanup), so callers can react."""
        # Discord can't create an event in the past, and editing an in-progress
        # event's timing is pointless. Leave anything already started alone.
        if ev['_start'] <= now:
            return False

        desired = self._desired(ev, default_location)
        signature = self._signature(desired)
        link = links.get(gid)
        existing = discord_events.get(link.discord_id) if link else None

        changed = False

        # Reconcile against our own untracked events sharing this event's identity:
        # adopt one if we lost the link (prevents a duplicate), and delete the rest
        # (cleans up duplicates a past crash already created).
        candidates = orphans.get(self._identity(desired['name'], desired['start_time']))
        if candidates:
            if existing is None:
                existing = candidates.pop(0)
                link = None  # newly adopted -> force the link (re)write below
            for dupe in candidates:
                if existing is not None and dupe.id == existing.id:
                    continue
                if dupe.status is discord.EventStatus.scheduled:
                    try:
                        await dupe.delete(reason='Removing duplicate synced event')
                        logging.info(f"Deleted duplicate Discord event {dupe.id} for {gid}")
                        changed = True
                    except discord.DiscordException:
                        logging.exception(f"Failed to delete duplicate event {dupe.id}")
            orphans[self._identity(desired['name'], desired['start_time'])] = []

        try:
            if existing is None:
                # New event, or a synced one that was manually deleted in Discord.
                created = await guild.create_scheduled_event(
                    name=desired['name'],
                    description=desired['description'],
                    start_time=desired['start_time'],
                    end_time=desired['end_time'],
                    location=desired['location'],
                    entity_type=discord.EntityType.external,
                    privacy_level=discord.PrivacyLevel.guild_only,
                    reason='Synced from Google Calendar',
                )
                await self.store.upsert(calendar_id, gid, created.id, signature)
                logging.info(f"Created Discord event for {gid}")
                return True
            # We have an event (linked or freshly adopted). Record/refresh the link
            # if it's newly adopted or the source content changed.
            if link is None or link.signature != signature:
                await existing.edit(
                    name=desired['name'],
                    description=desired['description'],
                    start_time=desired['start_time'],
                    end_time=desired['end_time'],
                    location=desired['location'],
                    reason='Updated from Google Calendar',
                )
                await self.store.upsert(calendar_id, gid, existing.id, signature)
                logging.info(f"{'Adopted' if link is None else 'Updated'} Discord event for {gid}")
                return True
            # else: signature unchanged -> nothing to do.
        except discord.DiscordException:
            logging.exception(f"Failed to create/update Discord event for {gid}")
        return changed

    # ---------------------------------------------------------------- google I/O

    async def _fetch_events(self, service, calendar_id, time_min, time_max):
        def _list():
            return service.events().list(
                calendarId=calendar_id,
                singleEvents=True,
                orderBy='startTime',
                timeMin=time_min.isoformat(timespec='seconds'),
                timeMax=time_max.isoformat(timespec='seconds'),
                maxResults=MAX_EVENTS,
            ).execute()

        result = await asyncio.to_thread(_list)
        events = [i for i in result.get('items', []) if i.get('kind') == 'calendar#event']
        for ev in events:
            self._normalize_times(ev)
        return events

    async def _get_event(self, service, calendar_id, event_id):
        """Fetch one event; None if it's been deleted (404/410) or cancelled."""
        def _get():
            try:
                return service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            except HttpError as err:
                if err.resp.status in (404, 410):
                    return None
                raise

        ev = await asyncio.to_thread(_get)
        if ev is None or ev.get('status') == 'cancelled':
            return None
        self._normalize_times(ev)
        return ev

    # ---------------------------------------------------------------- mapping

    @staticmethod
    def _normalize_times(ev):
        """Attach timezone-aware _start / _end. All-day events use date fields
        (Google's all-day end date is exclusive, which gives a clean full span)."""
        start, end = ev['start'], ev.get('end', {})
        if 'dateTime' in start:
            ev['_start'] = datetime.fromisoformat(start['dateTime'])
        else:
            ev['_start'] = datetime.strptime(start['date'], '%Y-%m-%d').replace(tzinfo=LOCAL_TZ)

        if 'dateTime' in end:
            ev['_end'] = datetime.fromisoformat(end['dateTime'])
        elif 'date' in end:
            ev['_end'] = datetime.strptime(end['date'], '%Y-%m-%d').replace(tzinfo=LOCAL_TZ)
        else:
            ev['_end'] = ev['_start'] + timedelta(hours=1)

        # Discord requires end strictly after start.
        if ev['_end'] <= ev['_start']:
            ev['_end'] = ev['_start'] + timedelta(hours=1)

    @staticmethod
    def _desired(ev, default_location):
        name = (ev.get('summary') or '(untitled)').strip()[:NAME_LIMIT]
        description = _strip_html(ev.get('description', ''))[:DESCRIPTION_LIMIT]
        location = ((ev.get('location') or '').strip() or default_location)[:LOCATION_LIMIT]
        return {
            'name': name or '(untitled)',
            'description': description,
            'start_time': ev['_start'],
            'end_time': ev['_end'],
            'location': location or default_location,
        }

    @staticmethod
    def _signature(desired):
        raw = '|'.join([
            desired['name'],
            desired['description'],
            desired['start_time'].isoformat(),
            desired['end_time'].isoformat(),
            desired['location'],
        ])
        return hashlib.sha1(raw.encode('utf-8')).hexdigest()

    @staticmethod
    def _identity(name, start_time):
        """Stable key for matching one of our own scheduled events to a calendar
        event across restarts: the (truncated) name plus the start instant. Both
        sides are set by us, so they line up; normalized to UTC to be tz-safe."""
        return (name, start_time.astimezone(timezone.utc).replace(microsecond=0))
