import logging
import discord
import asyncio
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
from dateutil import tz
from calendar import monthrange
from requests.structures import CaseInsensitiveDict

SCOPES = ['https://www.googleapis.com/auth/calendar']
# Poll interval in seconds
CALENDAR_POLL_INTERVAL = 30

EASTERN_TIMEZONE = tz.gettz('America/New_York')
UTC_TIMEZONE = tz.tzutc()

class ReminderManager():
    def __init__(self, bot, google_creds, relay_map):
        self.bot = bot
        self.google_creds = google_creds
        self.relay_map = CaseInsensitiveDict(relay_map)
        self.tasks = []


    async def initialize(self):
        logging.info("Initializing google calendar reminders...")
        # For each google calendar, create a new async task that will poll for upcoming events
        for calendar_label in self.relay_map.keys():
            config = self.relay_map[calendar_label]
            
            calendar_id = config['calendar_id']
            channel_ids = config['channels']
            # This is a list of integers representing how many minutes prior to the event do we want a reminder
            when_to_notify = config['when']
            ping = config['ping']

            channels = []
            for channel_id in channel_ids:
                channel = discord.utils.get(self.bot.get_all_channels(), id=int(channel_id))
                if not channel:
                    logging.warning(f"Bot does not have access to channel with ID {channel_id}!")
                else:
                    channels.append(channel)
            logging.info(f"  Watching for events from Google Calendar {calendar_label} with id {calendar_id}...")
            self.tasks.append(asyncio.create_task(self.poll_calendar_events(calendar_id, channels, when_to_notify, ping)))
        logging.info("Done.")


    async def auth(self):
        # Grabs an authenticated endpoint for pulling calendar data
        credentials = service_account.Credentials.from_service_account_file(self.google_creds, scopes=SCOPES)
        return build('calendar', 'v3', credentials=credentials, cache_discovery=False)


    async def poll_calendar_events(self, calendar_id, channels, when_to_notify, ping):
        # This cache is local to only this running coroutine
        # We only want to notify an event for a particular "minutes until event" trigger one time
        cache = {}
        for look_ahead in when_to_notify:
            cache[look_ahead] = set()

        while True:
            # Grab a timezone-aware timestamp for "now", in UTC time
            right_now = datetime.now(timezone.utc)
            for look_ahead in when_to_notify:
                # Based on the "minutes until event" value, create a "look ahead" window of a minute
                # Ex: imagine a value of "2" for "2 minutes before event, notify"
                #
                #         now                              look ahead window
                #      (12:00:30)                          |---------------|
                #   |--------------|---------------|--------------|--------------|
                # 12:00          12:01           12:02          12:03          12:04
                #
                # TODO: This isn't ideal. This window of one minute means that we're notifying
                # up to a minute too soon, depending on where "now" falls on the seconds clock.
                start_after = right_now + timedelta(minutes=look_ahead)
                start_before = start_after + timedelta(minutes=1)
                for channel in channels:
                    newline = "\n"
                    await self.get_events_in_window(
                        calendar_id, start_after, start_before, channel, look_ahead, cache[look_ahead],
                        f'📅  🐱 💬  {"@here " if ping else ""}' +
                            f'Events are starting {"in " + str(look_ahead) + " minutes" if look_ahead > 0 else "now"}!'
                    )
            await asyncio.sleep(CALENDAR_POLL_INTERVAL)


    async def get_events_in_window(self, calendar_id, start_after, start_before, channel, look_ahead, cache, prompt):
        try:
            # Construct the query to Google Calendar. We're only able to provide a cutoff for starting after a date.
            # So we'll validate the "starting before" the other end of the window later.
            service = await self.auth()
            result = service.events().list(
                calendarId=calendar_id,
                singleEvents=True,
                orderBy='startTime',
                timeMin=f'{start_after.isoformat(timespec="seconds")}',
                maxResults=5
            ).execute()

            # Edge case - no data comes back
            if 'items' not in result:
                return

            # Filter out anything that's not an event
            future_events = [item for item in result['items'] if 'kind' in item and item['kind'] == 'calendar#event']

            # Filter out anything that's already in the cache
            uncached_future_events = [item for item in future_events if item['id'] not in cache]
            if not uncached_future_events:
                return

            # Some events may only have a date. This just converts those dates to datetime objects (midnight on date).
            await self._change_events_start_date_to_datetime(future_events)

            # Filter out anything that's not actually in the "window"
            to_notify = []
            for future_event in future_events:
                start = future_event['start']
                when = datetime.fromisoformat(future_event['start']['dateTime'])
                if when >= start_after and when < start_before:
                    to_notify.append(future_event)
            if not to_notify:
                return

            # Construct the message for the notification.
            msg = f"{prompt}\n```"
            for future_event in to_notify:
                start = future_event['start']
                when = datetime.fromisoformat(future_event['start']['dateTime'])
                logging.debug(f'  {start_after.isoformat(timespec="seconds")}  -  {start_before.isoformat(timespec="seconds")}')
                logging.info(f"  Event reminder being sent from calendar {calendar_id} to channel {channel.id}.")
                cache.add(future_event['id'])
                when_str = when.strftime("%A, %d. %B %Y %I:%M%p %Z")
                msg += f'{future_event["summary"]} - {when_str}\n'
            msg += '```'
            await channel.send(msg)
        except Exception as e:
            logging.exception(f'Exception thrown while attempting to check events on calendar {calendar_id} for channel {channel.id}')


    async def get_upcoming_events(self, channel, calendar_name=None):
        if not channel:
            return

        if not calendar_name or calendar_name not in self.relay_map:
            newline = "\n" # fstring quirk
            await channel.send(f'🙀  I only know of these calendars.\n```{newline.join(self.relay_map.keys())}```')
            return
        
        calendar_id = self.relay_map[calendar_name]['calendar_id']
        right_now = right_now = datetime.now(timezone.utc)
        
        # Calculate the number of days to add to get to the last day of next month.
        # There's probably a better way to do this, because this really stinks.
        next_month_year = right_now.year
        next_month = right_now.month + 1
        if next_month == 13:
            next_month_year += 1
            next_month = 1
        days_in_next_month = monthrange(next_month_year, next_month)[1]
        days_in_this_month = monthrange(right_now.year, right_now.month)[1]
        total_days_add = (days_in_this_month - right_now.day) + days_in_next_month
        last_day_of_next_month = right_now + timedelta(days=+total_days_add)

        # Okay, now we have SOME time ON the last day. Let's cut that time off, and
        # force it to be the last second of the day.
        last_day_of_next_month.strftime('%Y-%m-%d') + 'T23:59:59Z'

        service = await self.auth()
        result = service.events().list(
            calendarId=calendar_id,
            singleEvents=True,
            orderBy='startTime',
            timeMin=f'{right_now.isoformat(timespec="seconds")}',
            timeMax=f'{last_day_of_next_month.strftime("%Y-%m-%d")}T23:59:59Z',
            maxResults=20
        ).execute()
        if not 'items' in result:
            return

        future_events = [item for item in result['items'] if 'kind' in item and item['kind'] == 'calendar#event']
        if not future_events:
            return

        # Convert all events with only dates to have datetimes starting at midnight
        await self._change_events_start_date_to_datetime(future_events)
        future_events = sorted(future_events, key=lambda item: datetime.fromisoformat(item['start']['dateTime']))

        msg = "📅  🐱 💬  There are some meetings and events coming up...\n```"
        for future_event in future_events:
            start = future_event['start']
            when = datetime.fromisoformat(future_event['start']['dateTime']).astimezone(EASTERN_TIMEZONE)
            when_str = when.strftime("%A, %d. %B %Y %I:%M%p %Z").replace('12:00AM EST', '')
            msg += f'{future_event["summary"]} - {when_str}\n'
        msg += '```'
        await channel.send(msg)


    async def _change_events_start_date_to_datetime(self, future_events):
        for future_event in future_events:
            start = future_event['start']
            if 'date' in start:
                new_start = {'dateTime': start['date'] + 'T00:00:00-00:00'}
                future_event['start'] = new_start