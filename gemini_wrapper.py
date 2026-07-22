import asyncio
import discord
import logging
from google import genai
from google.genai import types



PROMPT_PREFIX = "You are a discord bot present in the Tavern Troupe Performing Order discord. Your persona is one that should embrace cat-ness, as the bot is named after its author's cat. "
PROMPT_SHORT_SUFFIX = "Keep your response concise, like a real chat message."
SOLICITED_RESPONSE_PROMPT = (
    PROMPT_PREFIX +
    "The following content are the last bunch of messages for context. Chime in on the conversation, playfully. " +
    "If the most recent message in the history is a question, answer the question given. " +
    PROMPT_SHORT_SUFFIX
)

TOAST_RESPONSE_PROMPT = (
    PROMPT_PREFIX +
    "Someone is asking you to say something nice about them! Make it witty, befitting of a bard colleague, with bonus points for wordplay and puns." +
    PROMPT_SHORT_SUFFIX
)

ROAST_RESPONSE_PROMPT = (
    PROMPT_PREFIX +
    "Someone is asking you to roast them! Come up with something saucy, witty, and mean in a Shakespearean style." +
    PROMPT_SHORT_SUFFIX
)

JOKE_RESPONSE_PROMPT = (
    PROMPT_PREFIX +
    "Someone is asking you to tell a joke! Tell us a joke, with the punch-line included. It should be witty, in the theme of your identity." +
    PROMPT_SHORT_SUFFIX
)


async def get_gemini_response(source_message, gemini_prompt, context_window=0, skip_first_message=True, gemini_key=None, gemini_model=None):
    # Let the user know the bot is working on a response (only if it's a real message, not an adapter).
    async with source_message.channel.typing():
        try:
            # We build the full payload for the API, including the prompt, message history, and any images.
            contents = [gemini_prompt]

            # Fetch the last `history` messages for context (to include the command message).
            if context_window > 0:
                history = [msg async for msg in source_message.channel.history(limit=context_window)]    
                # The first message is the '!thoughts' command itself, so we remove it.
                if skip_first_message:
                    history.pop(0)
                
                # The history is fetched newest-to-oldest, so we reverse it for chronological order.
                history.reverse()

                for msg in history:
                    # Add the message's text content, prefixed with the author's display name.
                    contents.append(f"**{msg.author.display_name}**: {msg.content}")

                    # If the message has attachments, process them.
                    if msg.attachments:
                        for attachment in msg.attachments:
                            # Check if the attachment is an image.
                            if attachment.content_type and attachment.content_type.startswith('image/'):
                                image_bytes = await attachment.read()
                                # Add the image data to the payload.
                                contents.append({
                                    "inline_data": {
                                        "mime_type": attachment.content_type,
                                        "data": image_bytes
                                    }
                                })
            
            # Configure the generative AI model. We use a model that supports multimodal (text and image) input.
            if not gemini_key or not gemini_model:
                raise ValueError("gemini_key and gemini_model must be provided")
            client = genai.Client(api_key=gemini_key)

            # Call the Gemini API to generate a response. The client is blocking,
            # so keep it off the event loop or reminder timers/other commands stall.
            response = await asyncio.to_thread(
                lambda: client.models.generate_content(
                    model=gemini_model,
                    contents=contents
                )
            )
            
            # Send the generated text back to the Discord channel.
            return response.text

        except Exception as e:
            logging.error(f"Error with command (input: {source_message.content}): {e}")
            return "😿 My brain just short-circuited... try again in a bit."