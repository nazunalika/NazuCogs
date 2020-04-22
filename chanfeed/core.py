from __future__ import annotations

import asyncio
import logging
import string
from datetime import datetime
from typing import Optional, List, Dict, Any

import aiohttp
import discord

import discordtextsanitizer as dts
from redbot.core import commands, checks
from redbot.core.config import Config
from redbot.core.utils.chat_formatting import pagify

from .cleanup import html_to_text
from .converters import TriState

# We need this to interact with 4chan's API
import basc_py4chan
import re

# These imports may not be needed
import feedparser

log = logging.getLogger("red.nazucogs.chanfeed")
DONT_HTML_SCRUB = ["link", "source", "updated", "updated_parsed"]

def debug_exc_log(lg: logging.Logger, exc: Exception, msg: str = "Exception in Chan Feed"):
    if lg.getEffectiveLevel() <= logging.DEBUG:
            lg.exception(msg, exc_info=exc)

class ChanFeed(commands.Cog):
    """
    This is a 4chan feed cog

    This cog has limited support but I will try my best to assist users in
    fixing any issues that may occur.
    """

    __author__ = "nazunalika (Sokel)"
    __version__ = "330.0.1"

    # help formatter
    def format_help_for_context(self, ctx):
        pre_process = super().format_help_for_context(ctx)
        return f"{pre_process}\nVersion: {self.__version__}"

    # initial bootstrap
    def __init__(self, bot, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=99123337941934777, force_registration=True
        )
        self.config.register_channel(feeds={})
        self.session = aiohttp.ClientSession()
        self.bg_loop_task: Optional[asyncio.Task] = None

    # background sync
    def init(self):
        self.bg_loop_task = asyncio.create_task(self.bg_loop())
        def done_callback(fut: asyncio.Future):
            try:
                fut.exception()
            except asyncio.CancelledError:
                pass
            except asyncio.InvalidStateError as exc:
                log.exception(
                    "We're not done but we did a callback?", exc_info=exc
                )
            except Exception as exc:
                log.exception("Unexpected exception in chanfeed: ", exc_info=exc)

        self.bg_loop_task.add_done_callback(done_callback)

    # unload
    def cog_unload(self):
        if self.bg_loop_task:
            self.bg_loop_task.cancel()
        asyncio.create_task(self.session.close())

    # fetch the feed here
    # Check that the board exists and then check the thread exists
    async def fetch_feed(self, url: str)
        timeout = aiohttp.client.ClientTimeout(total=15)
        # SPLIT OUT THE URL HERE
        urlSplit = url.rsplit('/', 3)
        board = urlSplit[1]
        thread = urlSplit[3]
        # We don't really need this right now unless I decide to do a full
        # "built-in" of the py4chan plugin. But it's good to know if we can
        # connect or not and bomb out when we can't.
        urlGeneration = 'https://a.4cdn.org/' + board + '/thread/' + thread + '.json'
        try:
            async with self.session.get(urlGeneration, timeout=timeout) as response:
                data = await response.read()
            chanboard = basc_py4chan.Board(board)
            chanthread = chanboard.get_thread(thread)
            if chanboard.title is not None:
                if chanthread.id is not None:
                    pass
        except (aiohttp.ClientError, asyncio.TimeoutError):
            # We couldn't connect
            return None
        except KeyError:
            # The board doesn't exist
            return None
        except AttributeError:
            # The thread doesn't exist
            return None
        except Exception as exc:
            debug_exc_log(
                    log,
                    exc,
                    f"Unexpected exception type {type(exc)} encountered for thread {board} -> {thread}",
            )
            return None

        if chanthread.archived:
            # The thread is archived
            log.debug(f"{board} -> {thread} is archived and is not considered valid.")
            return None
        return chanthread

    #@staticmethod
    #def process_post_number_and_id(x):

    #async def format_and_send(
    #        self,
    #        *,
    #        destination: discord.TextChannel,
    #        #response: chanthread,
    #        embed_default: bool,

    def format_post(
            self,
            entry,
            embed: bool,
            color,
        ) -> dict:

        #board = basc_py4chan.Board(entry.board)
        #thread = board.get_thread(entry.thread)
        # create vars for all relevant pieces of the embed
        #postID  = reply.id
        #content = reply.text_comment

        if embed:
            if len(content) > 2000:
                content = content[:1999] + "... (post is too long)"
            # . . .
            # Start the embed here ...
            # . . .
            return {"content": None, "embed": embed_data}
        else:
            if len(content) > 2000:
                content = content[:1900] + "... (post is too long)"
            return {"content": content, "embed": None}

    # Commands
    @checks.mod_or_permissions(manage_channels=True)
    @commands.guild_only()
    @commands.group()
    async def chanfeed(self, ctx: commands.GuildContext):
        """
        Configuration for chanfeed
        """
        pass

    @commands.cooldown(3, 60, type=commands.BucketType.user)
    @chanfeed.command(command="add")
    async def add_feed(
            self,
            ctx: commands.GuildContext,
            name: str,
            url: str,
            channel: Optional[discord.TextChannel] = None,
    ):

        """
        Adds a 4chan thread feed to the current or provided channel
        """

        channel = channel or ctx.channel

        async with self.config.channel(channel).feeds() as feeds:
            if name in feeds:
                return await ctx.send(f"{name}: That name is already in use, please choose another")

            response = await self.fetch_feed(url)

            if response is None:
                return await ctx.send(
                    f"That doesn't appear to be a valid thread. "
                    f"(Syntax: {ctx.prefix}{ctx.command.signature})"
                )

            else:
                lastCurrentPost = response.last_reply_id
                feeds.update(
                    {
                        name: {
                            "url": url,
                            "embed_override": None,
                            "lastPostID": lastCurrentPost,
                        }
                    }
                )

        await ctx.tick()

    @chanfeed.command(name="remove")
    async def remove_feed(
            self,
            ctx,
            name: str,
            channel: Optional[discord.TextChannel] = None,
    ):
        """
        Removes a thread feed from the current channel or from a provided channel.

        If the feed is in the process of being fetched, there could be one final
        update that appears, unless the thread is in an archived state or 404.
        """

        channel = channel or ctx.channel
        async with self.config.channel(channel).feeds() as feeds:
            if name not in feeds:
                await ctx.send(f"{name}: There is no feed with that name in {channel.mention}.")
                return

            del feeds[name]

        await ctx.tick()

    @chanfeed.command(name="embed")
    async def set_embed(
        self,
        ctx,
        name: str,
        setting: TriState,
        channel: Optional[discord.TextChannel] = None,
    ):
        """
        Sets if a feed should use or not use an embed. This uses the default bot
        setting if not set.

        Only accepts: True, False, Default
        """

        channel = channel or ctx.channel

        async with self.config.channel(channel).feeds() as feeds:
            if name not in feeds:
                await ctx.send(f"{name}: No feed with that name in {channel.mention}.")
                return

            feeds[name]["embed_override"] = setting.state

        await ctx.tick()

    @chanfeed.command(name="force")
    async def force_feed(
            self,
            ctx,
            feed,
            channel: Optional[discord.TextChannel] = None
    ):
        """
        Forces the latest post for a thread
        """

        channel = channel or ctx.channel
        feeds = await self.config.channel(channel).feeds()
        url = None

        if feed in feeds:
            url = feeds[feed].get("url", None)

        if url is None:
            return await ctx.send("There is no such feed available. Try your call again later.")

        response = await self.fetch_feed(url)

        # Like another section, if we get "None" then we're not valid
        # That's just how it has to be
        if response:
            should_embed = await self.should_embed(ctx.channel)

            try:
                await self.format_and_send(
                        destination=channel,
                        response=response,
                        embed_default=should_embed,
                        force=True,
                )
            except Exception:
                await ctx.send("We caught an error with your request. Try your call again later.")
            else:
                await ctx.tick()
        else:
            await ctx.send("That doesn't appear to be a valid thread.")
