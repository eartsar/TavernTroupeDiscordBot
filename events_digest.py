"""
EventsDigest

Maintains a single, self-updating "upcoming events" summary message in a Discord
channel, mirroring a Google Calendar. The message is a rich embed (colored left
margin), grouped by date. It behaves like the calendar bot it replaces:

  * A fresh summary is posted once per week (so it bumps to the bottom).
  * Between weekly posts, the current message is edited in place whenever the
    calendar changes, so it always reflects the live schedule.

The posted message's id is persisted (EVENTS_DIGEST table) so the digest survives
process restarts instead of orphaning the previous message and posting a new one.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

import discord
from dateutil import tz
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

POLL_INTERVAL = 300               # seconds between refresh passes
DEFAULT_HORIZON_DAYS = 60         # how far ahead to list
REPOST_INTERVAL = timedelta(days=7)   # post a new message this often
MAX_EVENTS = 250
EMBED_DESC_LIMIT = 4096           # Discord embed description hard cap

EASTERN = tz.gettz('America/New_York')

DEFAULT_TITLE = 'Upcoming Events'
DEFAULT_COLOR = '8e24aa'          # the purple left margin, matching the old bot
DEFAULT_EMOJI = '🎭'
DEFAULT_NAME_WIDTH = 44           # monospace columns the event name is padded to
# Thin gap between date groups. A subtext line (-#) renders shorter than a normal
# blank line -- the closest Discord gets to "a few pixels". Set '' for none, or
# a lone '\u200b' for a full blank line.
DEFAULT_GROUP_SEP = '-# \u200b'


def _fmt_date(dt):
    # "Monday, June 29, 2026" -- %-d isn't portable, so assemble the day manually.
    return dt.strftime('%A, %B ') + str(dt.day) + dt.strftime(', %Y')


class EventsDigest:
    def __init__(self, bot, google_creds, config, db):
        self.bot = bot
        self.google_creds = google_creds
        self.cfg = dict(config)
        self.db = db
        self.task = None
        # Set by external triggers (e.g. EventSyncer after it mirrors events) to
        # make the loop re-render promptly instead of waiting out the poll interval.
        self._wake = asyncio.Event()

    def request_refresh(self):
        """Nudge the digest to re-render on its next tick. Safe to call from other
        tasks; it just flips an event the loop is waiting on."""
        self._wake.set()

    async def initialize(self):
        logging.info("Initializing Google Calendar -> Discord events digest...")
        await self.db.initialize()  # idempotent; ensures EVENTS_DIGEST exists
        self.task = asyncio.create_task(self._loop())
        logging.info("Done.")

    def _build_service(self):
        creds = service_account.Credentials.from_service_account_file(
            self.google_creds, scopes=SCOPES
        )
        return build('calendar', 'v3', credentials=creds, cache_discovery=False)

    async def _loop(self):
        service = None
        while True:
            # Consume any pending nudge before rendering, so a nudge that arrives
            # *during* the refresh below still triggers another prompt pass.
            self._wake.clear()
            try:
                if service is None:
                    service = await asyncio.to_thread(self._build_service)
                await self._refresh(service)
            except Exception:
                logging.exception("Error updating events digest")
            # Wake early on an external nudge, otherwise fall through every interval.
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def _refresh(self, service):
        channel = self.bot.get_channel(int(self.cfg['channel_id']))
        if channel is None:
            logging.warning(f"Events digest channel {self.cfg['channel_id']} not available; skipping pass.")
            return

        horizon_days = self.cfg.get('horizon_days', DEFAULT_HORIZON_DAYS)
        now = datetime.now(timezone.utc)
        events = await self._fetch_events(
            service, self.cfg['calendar_id'], now, now + timedelta(days=horizon_days)
        )

        # Discord scheduled-event ids (from EventSyncer) let each name link to its
        # event card. Absent (sync off / not yet mirrored) -> name renders unlinked.
        guild_id = channel.guild.id if channel.guild else None
        link_rows = await self.db.get_event_links(self.cfg['calendar_id'])
        links = {google_id: discord_id for (google_id, discord_id, _sig) in link_rows}

        embed = self._build_embed(events, links, guild_id, horizon_days)
        signature = self._signature(embed)
        state = await self.db.get_digest_state(channel.id)

        # Decide whether to reuse the current message or post a fresh one.
        message = None
        if state is not None:
            posted_at = datetime.fromisoformat(state['posted_at'])
            if now - posted_at < REPOST_INTERVAL:
                try:
                    message = await channel.fetch_message(int(state['message_id']))
                except discord.NotFound:
                    message = None  # was deleted -> repost below

        if message is None:
            sent = await channel.send(embed=embed)
            await self.db.upsert_digest_state(channel.id, sent.id, now.isoformat(), signature)
            logging.info(f"Posted new events digest to channel {channel.id} (message {sent.id}).")
        elif state['signature'] != signature:
            await message.edit(embed=embed)
            # Keep the original posted_at so the weekly repost cadence is preserved.
            await self.db.upsert_digest_state(channel.id, message.id, state['posted_at'], signature)
            logging.info(f"Updated events digest message {message.id} in channel {channel.id}.")
        # else: nothing changed -> leave it be (avoids needless edits/rate limits).

    # ---------------------------------------------------------------- rendering

    def _build_embed(self, events, links, guild_id, horizon_days):
        title = self.cfg.get('title', DEFAULT_TITLE)
        emoji = self.cfg.get('event_emoji', DEFAULT_EMOJI)
        color = self._parse_color(self.cfg.get('color', DEFAULT_COLOR))
        name_width = int(self.cfg.get('name_width', DEFAULT_NAME_WIDTH))

        header = f"Events for the next {horizon_days} days"
        if not events:
            desc = f"{header}\n\nNothing on the calendar right now."
            return discord.Embed(title=title, description=desc[:EMBED_DESC_LIMIT], color=color)

        group_sep = self.cfg.get('group_separator', DEFAULT_GROUP_SEP)

        events.sort(key=lambda e: e['_start'])
        lines = []
        current_date = None
        for ev in events:
            start_local = ev['_start'].astimezone(EASTERN)
            day = start_local.date()
            if day != current_date:
                if current_date is not None and group_sep:
                    lines.append(group_sep)  # thin gap between date groups
                current_date = day
                lines.append(f"**{_fmt_date(start_local)}**")
            lines.append(self._event_line(ev, links, guild_id, emoji, name_width))

        desc = header + '\n' + '\n'.join(lines)
        return discord.Embed(title=title, description=desc[:EMBED_DESC_LIMIT], color=color)

    @staticmethod
    def _event_line(ev, links, guild_id, emoji, name_width):
        """One line: emoji, the event name as an inline-code-styled link (monospace
        'boxed' text padded to a fixed width so times align), then a Discord
        timestamp range that localizes per viewer. No code block involved."""
        raw = (ev.get('summary') or '(untitled)').strip()
        # These would break the inline-code span or the markdown link syntax.
        raw = raw.replace('`', "'").replace('[', '(').replace(']', ')')
        if len(raw) > name_width:
            padded = raw[:name_width - 1] + '…'
        else:
            padded = raw.ljust(name_width)
        boxed = f"`{padded}`"

        discord_id = links.get(ev['id'])
        if discord_id and guild_id:
            name = f"[{boxed}](https://discord.com/events/{guild_id}/{discord_id})"
        else:
            name = boxed  # still monospace-boxed, just not clickable

        if ev.get('_all_day'):
            when = 'All day'
        else:
            start_ts = int(ev['_start'].timestamp())
            end_ts = int(ev['_end'].timestamp())
            when = f"<t:{start_ts}:t> - <t:{end_ts}:t>"

        return f"{emoji} {name}  {when}"

    @staticmethod
    def _parse_color(value):
        try:
            return discord.Color(int(str(value).lstrip('#'), 16))
        except (ValueError, TypeError):
            return discord.Color(int(DEFAULT_COLOR, 16))

    @staticmethod
    def _signature(embed):
        raw = f"{embed.title}\n{embed.description}\n{embed.color}"
        return hashlib.sha1(raw.encode('utf-8')).hexdigest()

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
        events = [
            i for i in result.get('items', [])
            if i.get('kind') == 'calendar#event' and i.get('status') != 'cancelled'
        ]
        for ev in events:
            self._normalize_times(ev)
        return events

    @staticmethod
    def _normalize_times(ev):
        """Attach timezone-aware _start / _end and an _all_day flag. All-day dates
        are local to the calendar (anchored to EASTERN), not UTC."""
        start, end = ev['start'], ev.get('end', {})
        if 'dateTime' in start:
            ev['_start'] = datetime.fromisoformat(start['dateTime'])
            ev['_all_day'] = False
        else:
            ev['_start'] = datetime.strptime(start['date'], '%Y-%m-%d').replace(tzinfo=EASTERN)
            ev['_all_day'] = True

        if 'dateTime' in end:
            ev['_end'] = datetime.fromisoformat(end['dateTime'])
        elif 'date' in end:
            ev['_end'] = datetime.strptime(end['date'], '%Y-%m-%d').replace(tzinfo=EASTERN)
        else:
            ev['_end'] = ev['_start'] + timedelta(hours=1)
