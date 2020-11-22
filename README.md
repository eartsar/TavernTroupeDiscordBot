## What is this?
It's a discord bot that I hacked together extremely quickly to fit the needs of DragonRealms' very own [Tavern Troupe Performing Order](https://taverntroupe.com/). Things like tweet relaying, event reminders, and the like.


## I want to run it.
1. [Make a Discord app](https://discordpy.readthedocs.io/en/latest/discord.html) and create a bot account for it.
2. Install the requirements. This project uses [poetry](https://python-poetry.org/), and includes a `pyproject.toml` file (`poetry install`). Note that python 3.7+ is *required*.
3. Get necessary credential files for the various components. This bot uses [Twitter's developer API][https://developer.twitter.com/en/apply-for-access] in order to pull tweets, and specifically uses [bearer tokens][https://developer.twitter.com/en/docs/authentication/oauth-2-0/bearer-tokens]. This bot also integrates with [Google's developer API][https://developers.google.com/] and expects a [service account][https://cloud.google.com/iam/docs/service-accounts] with proper permissions to have read access to any calendars being used.
4. Edit the `config.yml` file, and configure as needed.
5. Run the thing  
`python3 app.py --config /path/to/config.yml`

## Commands
    !ping                           Test command to ensure the bot is healthy.
    !events <calendar_name>         Pull up the events for the named calendar for this month and next month
                                    Keep calendar_name blank to get a list of calendars the bot knows of.
    !help

## TODOs
???

