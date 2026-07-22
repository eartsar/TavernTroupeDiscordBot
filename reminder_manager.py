import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from bs4 import BeautifulSoup
from dateutil import tz
from dateutil.relativedelta import relativedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from requests.structures import CaseInsensitiveDict

# Read-only is all we need to list events. Tighten the scope accordingly.
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

CALENDAR_POLL_INTERVAL = 300          # seconds between polls (discovery only)
NOTIFY_GRACE = timedelta(seconds=30)  # small fetch/timing margin
SCHEDULE_BUFFER = timedelta(seconds=5)
# Still send a (late) notice up to this long after an event's start; older than
# this we assume it's not worth surfacing (e.g. after a long outage/restart).
LATE_WINDOW = timedelta(minutes=10)
DISCORD_MSG_LIMIT = 2000              # hard cap on a single Discord message

EASTERN = tz.gettz('America/New_York')


def _strip_html(text):
    """Turn a calendar description that may contain HTML into plain text."""
    if not text:
        return ''
    if BeautifulSoup(text, 'html.parser').find():
        text = text.replace('<br>', '\n').replace('<wbr>', '').replace('&nbsp;', '')
        text = BeautifulSoup(text, 'lxml').text
    return text.strip()


class ReminderManager:
    def __init__(self, bot, google_creds, relay_map):
        self.bot = bot
        self.google_creds = google_creds
        self.relay_map = CaseInsensitiveDict(relay_map)
        self.tasks = []

    # ------------------------------------------------------------------ setup

    async def initialize(self):
        logging.info("Initializing Google Calendar reminders...")
        for label, config in self.relay_map.items():
            channels = self._resolve_channels(config['channels'])
            if not channels:
                logging.warning(f"\tNo reachable channels for '{label}'; skipping.")
                continue

            logging.info(f"\tWatching '{label}' ({config['calendar_id']})...")
            self.tasks.append(asyncio.create_task(
                self._poll_calendar(
                    calendar_id=config['calendar_id'],
                    channels=channels,
                    when_to_notify=sorted(config['when'], reverse=True),
                    ping=config.get('ping', False),
                )
            ))
        logging.info("Done.")

    def _build_service(self):
        """Build a fresh Google Calendar client. Called once per task (httplib2
        underneath is not thread-safe, so tasks must not share one)."""
        creds = service_account.Credentials.from_service_account_file(
            self.google_creds, scopes=SCOPES
        )
        return build('calendar', 'v3', credentials=creds, cache_discovery=False)

    def _resolve_channels(self, channel_ids):
        channels = []
        for channel_id in channel_ids:
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                channels.append(channel)
            else:
                logging.warning(f"\tBot cannot see channel {channel_id}!")
        return channels

    # ------------------------------------------------------------------ polling

    async def _poll_calendar(self, calendar_id, channels, when_to_notify, ping):
        logging.info(f"Poll watcher spawned for {calendar_id}.")
        service = None
        armed = {}       # (event_id, look_ahead) -> notify_at; dedup + pruning
        timers = set()   # live reminder timers, kept referenced so they aren't GC'd
        max_look_ahead = max(when_to_notify)
        # Look back far enough to bridge polls AND to still catch late notices.
        lookback = max(timedelta(seconds=CALENDAR_POLL_INTERVAL), LATE_WINDOW) + NOTIFY_GRACE

        while True:
            try:
                if service is None:
                    service = await asyncio.to_thread(self._build_service)

                now = datetime.now(timezone.utc)
                horizon = now + timedelta(seconds=CALENDAR_POLL_INTERVAL) + SCHEDULE_BUFFER
                events = await self._fetch_events(
                    service, calendar_id,
                    now - lookback,
                    horizon + timedelta(minutes=max_look_ahead),
                    20,
                )

                future_groups = {}   # (notify_at, look_ahead) -> [events], batched
                late_events = []     # events to notify about immediately (missed)
                for event in events:
                    start = event['_start']
                    pending = []
                    for look_ahead in when_to_notify:
                        key = (event['id'], look_ahead)
                        if key in armed:
                            continue
                        notify_at = (start - timedelta(minutes=look_ahead)).replace(microsecond=0)
                        if notify_at > horizon:
                            continue  # not due before next poll; arm it later
                        armed[key] = notify_at  # mark handled regardless of outcome
                        pending.append((look_ahead, notify_at))

                    if not pending:
                        continue

                    future = [(la, na) for (la, na) in pending if na > now]
                    for look_ahead, notify_at in future:
                        future_groups.setdefault((notify_at, look_ahead), []).append(event)

                    # A reminder was due but nothing on-time is still coming: send
                    # a late notice, unless the event started too long ago to bother.
                    if not future and (now - start) <= LATE_WINDOW:
                        late_events.append(event)

                for (notify_at, _look_ahead), due in future_groups.items():
                    self._schedule(timers, notify_at, channels, due, ping)
                for event in late_events:
                    self._schedule(timers, now, channels, [event], ping)

                self._prune(armed, now)
            except Exception:
                logging.exception(f"Error polling calendar {calendar_id}")

            await asyncio.sleep(CALENDAR_POLL_INTERVAL)

    def _schedule(self, timers, fire_time, channels, events, ping):
        task = asyncio.create_task(self._fire_at(fire_time, channels, events, ping))
        timers.add(task)
        task.add_done_callback(timers.discard)

    async def _fire_at(self, fire_time, channels, events, ping):
        """Sleep until the exact fire time, then send. Delay is recomputed against
        a fresh clock so there's no drift from arm time."""
        delay = (fire_time - datetime.now(timezone.utc)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        await self._send_reminder(channels, events, ping)

    async def _fetch_events(self, service, calendar_id, time_min, time_max=None, max_results=20):
        params = dict(
            calendarId=calendar_id,
            singleEvents=True,
            orderBy='startTime',
            timeMin=time_min.isoformat(timespec='seconds'),
            maxResults=max_results,
        )
        if time_max is not None:
            params['timeMax'] = time_max.isoformat(timespec='seconds')

        # .execute() is a blocking network call; keep it off the event loop so
        # reminder timers keep firing on schedule.
        result = await asyncio.to_thread(
            lambda: service.events().list(**params).execute()
        )

        events = [i for i in result.get('items', []) if i.get('kind') == 'calendar#event']
        for event in events:
            self._normalize_start(event)
        return events

    @staticmethod
    def _normalize_start(event):
        """Attach a parsed start datetime and an all-day flag to the event."""
        start = event['start']
        if 'dateTime' in start:
            event['_start'] = datetime.fromisoformat(start['dateTime'])
            event['_all_day'] = False
        else:  # all-day events carry only a 'date' (YYYY-MM-DD), local to the calendar
            midnight = datetime.strptime(start['date'], '%Y-%m-%d')
            event['_start'] = midnight.replace(tzinfo=EASTERN)
            event['_all_day'] = True

    @staticmethod
    def _prune(armed, now):
        cutoff = now - timedelta(hours=1)
        for key in [k for k, notify_at in armed.items() if notify_at < cutoff]:
            del armed[key]

    # ------------------------------------------------------------------ commands

    async def get_upcoming_events(self, send, calendar_name=None):
        """Send a summary of upcoming events. `send` is an async callable taking a
        single string (e.g. channel.send or interaction.followup.send), so callers
        that have already deferred an interaction get their response resolved."""
        if send is None:
            return

        known = '\n'.join(self.relay_map.keys())
        if not calendar_name:
            return await send(
                f'😾  Which one? Try `/events <calendar>` with one of these.\n```\n{known}\n```'
            )
        if calendar_name not in self.relay_map:
            return await send(f'🙀  I only know of these calendars.\n```\n{known}\n```')

        calendar_id = self.relay_map[calendar_name]['calendar_id']
        now = datetime.now(timezone.utc)
        # Last moment of next month, without the month-arithmetic gymnastics.
        end_of_next_month = (now + relativedelta(months=2)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(seconds=1)

        service = await asyncio.to_thread(self._build_service)
        events = await self._fetch_events(service, calendar_id, now, end_of_next_month, 20)
        if not events:
            return await send('📅  🐱 💬  Nothing on the calendar right now.')

        events.sort(key=lambda e: e['_start'])
        header = '📅  🐱 💬  There are some meetings and events coming up...'
        await send(self._build_message(header, events))

    # ------------------------------------------------------------------ rendering

    @staticmethod
    def _when_phrase(start, now):
        """Human phrasing for how far off the event is, computed live at send time."""
        minutes = round((start - now).total_seconds() / 60)
        if minutes > 1:
            return f"Starting in {minutes} minutes"
        if minutes == 1:
            return "Starting in 1 minute"
        if minutes == 0:
            return "Starting now"
        if minutes == -1:
            return "Started 1 minute ago"
        return f"Started {abs(minutes)} minutes ago"

    async def _send_reminder(self, channels, events, ping):
        now = datetime.now(timezone.utc)
        phrase = self._when_phrase(events[0]['_start'], now)  # events share a start
        prefix = "@here " if ping else ""
        header = f'📅  🐱 💬  {prefix}{phrase}!'
        message = self._build_message(header, events)

        for channel in channels:
            try:
                await channel.send(message)
                for event in events:
                    logging.info(f"Reminder {event['id']} ({phrase}) -> channel {channel.id}")
            except discord.DiscordException:
                logging.exception(f"Failed to send reminder to channel {channel.id}")

    def _build_message(self, header, events):
        """Assemble a message, stopping before Discord's length limit."""
        parts = [header]
        length = len(header)
        for event in events:
            block = f'```{self._render_event(event)}```'
            if length + len(block) + 1 > DISCORD_MSG_LIMIT:
                break
            parts.append(block)
            length += len(block) + 1
        return '\n'.join(parts)

    @staticmethod
    def _render_event(event):
        summary = event.get('summary', '(no title)')
        local_start = event['_start'].astimezone(EASTERN)
        if event.get('_all_day'):
            when_str = local_start.strftime('%A, %d %B %Y')
        else:
            when_str = local_start.strftime('%A, %d %B %Y %I:%M%p %Z')

        location = event.get('location', '')
        description = _strip_html(event.get('description', ''))

        lines = [f'{summary}    {when_str}']
        if location:
            lines.append(location)
        if description:
            lines.append(description)
        return '\n'.join(lines).strip()
