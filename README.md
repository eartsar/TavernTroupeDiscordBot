## What is this?
It's a discord bot that I hacked together extremely quickly to fit the needs of DragonRealms' very own [Tavern Troupe Performing Order](https://taverntroupe.com/). Things like tweet relaying, event reminders, and the like.


## I want to run it.
1. [Make a Discord app](https://discordpy.readthedocs.io/en/latest/discord.html) and create a bot account for it.
2. Install the requirements. This project uses [poetry](https://python-poetry.org/), and includes a `pyproject.toml` file (`poetry install`). Note that python 3.7+ is *required*.
3. Get necessary credential files for the various components. This bot uses [Twitter's developer API](https://developer.twitter.com/en/apply-for-access) in order to pull tweets, and specifically uses [bearer tokens](https://developer.twitter.com/en/docs/authentication/oauth-2-0/bearer-tokens). This bot also integrates with [Google's developer API](https://developers.google.com/) and expects a [service account](https://cloud.google.com/iam/docs/service-accounts) with proper permissions to have read access to any calendars being used.
4. Edit the `config.yml` file, and configure as needed.
5. Run the thing  
`python3 app.py --config /path/to/config.yml`

## Commands
	!ping                           Test command to ensure the bot is healthy.
	!help                           Displays this message.
	!nice                           Having a rough day? I'll say something nice!
	!joke                           ...Or tell you a joke!
	!petpic upload <name> [url]     Upload a picture to a pet album. This must be the comment on a file upload to the bot.
	                                    Files can be singular images of any type, or .zip archives.
	                                If url is supplied, the bot will attempt to download from it.
	                                Currently, direct links to zips, or share links from Google Drive and Dropbox work.
	!petpic random [name]           Show a random pet picture.
	                                    If name is supplied, show a random pic of that pet.
	!petpic list                    Shows a list of your albums
	!petpic list all                Shows a list of everyone's albums
	!petpic create <name>           Create a new album for a pet.
	!petpic delete <name>           Delete a pet album (and all associated pictures).
	!petpic wipe                    Delete ALL your pet pictures (asks confirmation).
	!events <calendar_name>         Pull up the events for the named calendar for this month and next month.
	                                Keep calendar_name blank to get a list of calendars the bot knows of.
	!log <start|stop>               Tells the troupe scribe to start or stop their note-taking (requires permission).

## TODOs
???

