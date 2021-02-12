import requests
from bs4 import BeautifulSoup

import discord


class FunManager():
    def __init__(self, bot, naughty_channels):
        self.bot = bot
        self.naughty_channels = set(naughty_channels)


    async def compliment(self, message):
        from app import SIGNATURE_EMOJI
        compliment = requests.get('https://complimentr.com/api').json()['compliment']
        return await message.channel.send(f"{SIGNATURE_EMOJI} {compliment}")


    async def joke(self, message):
        from app import SIGNATURE_EMOJI
        joke = requests.get('https://official-joke-api.appspot.com/jokes/random').json()
        return await message.channel.send(f"{SIGNATURE_EMOJI} {joke['setup']}\n    ...{joke['punchline']}")


    async def riddle(self, message):
        from app import SIGNATURE_EMOJI
        r = requests.get('https://fungenerators.com/random/riddle')
        soup = BeautifulSoup(r.content, 'html.parser')
        riddle = soup.find_all('h2', class_='wow fadeInUp animated')[0].text
        answer = soup.find_all('div', class_='answer-text')[0].text.strip()
        riddle_message = await message.channel.send(f"{SIGNATURE_EMOJI} Here's a riddle for you...\n>>> **{riddle}** \n\n*Answer\n||{answer}||*")


    async def roast(self, message):
        if not isinstance(message.channel, discord.channel.DMChannel) and message.channel.id not in self.naughty_channels:
            return await message.channel.send(f"{message.author.mention}, behave yourself!")
    
        from app import SIGNATURE_EMOJI
        r = requests.get('https://fungenerators.com/random/insult/shakespeare/')
        soup = BeautifulSoup(r.content, 'html.parser')
        return await message.channel.send(f"{message.author.mention}: {soup.find_all('h2', class_='wow fadeInUp animated')[0].text}")