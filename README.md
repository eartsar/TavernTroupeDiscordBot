## What is this?
It's a discord bot that I hacked together extremely quickly to fit the needs of DragonRealms' very own [Tavern Troupe Performing Order](https://taverntroupe.com/). Things like tweet relaying, event reminders, and the like.


## I want to run it.
1. [Make a bot account for Discord.](https://discordpy.readthedocs.io/en/latest/discord.html)
2. Install the requirements. This project uses [poetry](https://python-poetry.org/), and includes a `pyproject.toml` file (`poetry install`).
3. Edit the `config.yml` file, and configure as needed.
4. Run the thing  
`python3 app.py --config /path/to/config.yml`

## Commands
    !ping                           Test command to ensure the bot is healthy.

## TODOs
* Calendar integration with events reminder

