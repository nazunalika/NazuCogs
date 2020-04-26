from __future__ import annotations

import asyncio
import logging
import string
from datetime import datetime
from typing import Optional, List, Dict, Any

# Fixing import order
import re
import time
import aiohttp
import discord

#import discordtextsanitizer as dts
from redbot.core import commands, checks
from redbot.core.config import Config
from redbot.core.utils.chat_formatting import pagify

# We need this to interact with 4chan's API
import basc_py4chan

# cleanup stuff if we need it
#from .cleanup import html_to_text
from .converters import TriState
#from .utils import ChanThreadEntry

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

    # Check if we should embed
    async def should_embed(
            self,
            channel: discord.TextChannel,
    ) -> bool:
        ret: bool = await self.bot.embed_requested(channel, channel.guild.me)
        return ret

    # unload
    def cog_unload(self):
        if self.bg_loop_task:
            self.bg_loop_task.cancel()
        asyncio.create_task(self.session.close())

    @staticmethod
    def process_entry_timestamp(r):
        if r.timestamp:
            return tuple((time.gmtime(r.timestamp)))[:6]
        return (0,)

    @staticmethod
    def process_post_number(r):
        if r.number:
            return r.number
        return 0

    @staticmethod
    def url_splitter(data):
        urlSplit = data.rsplit('/', 3)
        output = {}
        output['board'] = urlSplit[1]
        output['thread'] = urlSplit[3]
        return output

    # fetch the feed here
    # Check that the board exists and then check the thread exists
    async def fetch_feed(self, url: str):
        timeout = aiohttp.client.ClientTimeout(total=15)
        # SPLIT OUT THE URL HERE
        split = self.url_splitter(url)
        # We don't really need this right now unless I decide to do a full
        # "built-in" of the py4chan plugin. But it's good to know if we can
        # connect or not and bomb out when we can't.
        urlGeneration = 'https://a.4cdn.org/' + split['board'] + '/thread/' + split['thread'] + '.json'
        try:
            async with self.session.get(urlGeneration, timeout=timeout) as response:
                data = await response.read()
            chanboard = basc_py4chan.Board(split['board'])
            chanthread = chanboard.get_thread(split['thread'])
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
                f"Unexpected exception type {type(exc)} encountered for {board} -> {thread}",
            )
            return None

        if chanthread.archived:
            # The thread is archived
            log.debug(f"The thread is archived and is not considered valid.")
            return None

        return chanthread

    async def format_and_send(
            self,
            *,
            destination: discord.TextChannel,
            response,
            feed_settings: dict,
            embed_default: bool,
            force: bool = False,
    ) -> Optional[List[int]]:
        """
        Formats and sends and it will update the config of the current number
        of replies, including the latest post ID. Those things will be used to
        determine if the thread has updated and to push the update to the
        channel later.
        """

        use_embed = feed_settings.get("embed_override", None)
        if use_embed is None:
            use_embed = embed_default

        # ERROR HANDLING PLEASE
        loopydata = {}
        lastCurrentPost = int(feed_settings.get("lastPostID", None))
        threadReplyNumber = int(feed_settings.get("numberOfPosts", None))
        newReplies = len(response.replies) - 1
        if response.last_reply_id > lastCurrentPost:
            loopydata['entries'] = []
            if newReplies > threadReplyNumber:
                howmany = [i for i in range(threadReplyNumber - newReplies, 0)]
                for k in howmany:
                    loopydata['entries'].append(response.replies[k])
            elif newReplies == threadReplyNumber:
                loopydata['entries'].append(response.replies[-1])
        elif force:
            loopydata['entries'] = []
            loopydata['entries'].append(response.replies[-1])
        else:
            return None

        assert isinstance(loopydata, dict), "mypy"
        assert isinstance(loopydata['entries'], list), "mypy"

        if force:
            try:
                to_send = [loopydata['entries'][-1]]
            except IndexError:
                return None
        else:
            # Eventually I want to do some sorting in a much better way
            to_send = sorted(
                [r for r in loopydata['entries'] if self.process_post_number(r) > lastCurrentPost],
                key=self.process_post_number,
            )

        last_sent = None
        for entry in to_send:
            color = destination.guild.me.color
            readypost = self.format_post(
                entry,
                use_embed,
                color,
            )
            try:
                await self.bot.send_filtered(destination, **readypost)
            except discord.HTTPException as exc:
                debug_exc_log(log, exc, "Caught exception while sending the feed.")
            last_sent = {'timestamp': list(self.process_entry_timestamp(entry)), 'postnumber': str(entry.number), 'posts': str(newReplies)}
            #last_sent = list(self.process_entry_timestamp(entry))

        return last_sent

    def format_post(
            self,
            entry,
            embed: bool,
            color,
    ) -> dict:

        # Eventually I want to get this to loop correctly, probably somewhere
        # else

        # ERROR HANDLING PLEASE
        # CHOOSE A BETTER NAME MAYBE
        reply = entry
        board = self.url_splitter(reply.url)['board']
        # create vars for all relevant pieces of the embed
        chanLogoImg = "https://i.imgur.com/xKI9j3H.png"
        postTimestamp = time.strftime('%m/%d/%y (%a) %H:%M:%S', time.localtime(reply.timestamp))
        postURL = reply.url
        posterID = reply.number
        posterName = reply.name
        poster = reply.poster_id or ""
        posterTrip = reply.tripcode or ""
        postComment = reply.comment
        clearComment = reply.text_comment
        thumbnailURL = reply.thumbnail_url
        threadURL = 'https://boards.4chan.org/%s/thread/%s' % (board, posterID)
        # Replace post references with full links to the post
        content = re.sub(r'(\#p\d+)', threadURL + r'\1', postComment)

        # Conditionals
        if reply.thumbnail_url:
            fieldNameOne = "<img src='%s'/>" % thumbnailURL
        else:
            fieldNameOne = " "

        embedTitle = "<img src='%s' style='width:20px;height:20px;'> %s %s %s" % (chanLogoImg, posterName, poster, posterTrip)
        embedDesc = "<a href='%s'>No. %s</a>" % (postURL, posterID)

        if embed:
            if len(content) > 2000:
                content = content[:1999] + "... (post is too long)"
            timestamp = datetime(*self.process_entry_timestamp(reply))
            embed_data = discord.Embed(title=embedTitle, description=embedDesc, color=color)
            embed_data.add_field(name=fieldNameOne, value=content, inline=False)
            embed_data.set_footer(text=postTimestamp)
            return {"content": None, "embed": embed_data}
        else:
            if len(content) > 2000:
                clearComment = clearComment[:1900] + "... (post is too long)"
            return {"content": clearComment, "embed": None}

    async def handle_response_from_loop(
            self,
            *,
            response,
            channel: discord.TextChannel,
            feed: dict,
            feed_name: str,
            should_embed: bool,
    ):
        if not response:
            return
        try:
            last = await self.format_and_send(
                destination=channel,
                response=response,
                feed_settings=feed,
                embed_default=should_embed,
            )
        except Exception as exc:
            debug_exc_log(log, exc)
        else:
            if last:
                await self.config.channel(channel).feeds.set_raw(
                    feed_name, "lastPostID", value=last['postnumber']
                )

                await self.config.channel(channel).feeds.set_raw(
                    feed_name, "numberOfPosts", value=last['posts']
                )

                await self.config.channel(channel).feeds.set_raw(
                    feed_name, "lastPostTimestamp", value=last['timestamp']
                )


    async def do_feeds(self):
        feeds_fetched: Dict[str, Any] = {}
        default_embed_settings: Dict[discord.Guild, bool] = {}
        channel_data = await self.config.all_channels()

        for channel_id, data in channel_data.items():
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            if channel.guild not in default_embed_settings:
                should_embed = await self.should_embed(channel)
                default_embed_settings[channel.guild] = should_embed
            else:
                should_embed = default_embed_settings[channel.guild]

            for feed_name, feed in data["feeds"].items():
                url = feed.get("url", None)
                if not url:
                    continue
                if url in feeds_fetched:
                    response = feeds_fetched[url]
                else:
                    response = await self.fetch_feed(url)
                    feeds_fetched[url] = response

                await self.handle_response_from_loop(
                    response=response,
                    channel=channel,
                    feed=feed,
                    feed_name=feed_name,
                    should_embed=should_embed,
                )

    async def bg_loop(self):
        await self.bot.wait_until_ready()
        while await asyncio.sleep(60, True):
            await self.do_feeds()

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
    @chanfeed.command()
    async def addfeed(
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
                    f"{name}: That doesn't appear to be a valid thread."
                )

            else:
                threadReplyNumber = len(response.replies) - 1
                lastReply = response.replies[threadReplyNumber]
                lastTimestamp = list(tuple((time.gmtime(lastReply.timestamp) or (0,)))[:7])

                feeds.update(
                    {
                        name: {
                            "url": url,
                            "embed_override": None,
                            "lastPostID": response.last_reply_id,
                            "numberOfPosts": threadReplyNumber,
                            "lastPostTimestamp": lastTimestamp,
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

    @chanfeed.command(name="list")
    async def list_feeds(
            self,
            ctx: commands.GuildContext,
            channel: Optional[discord.TextChannel] = None
    ):
        """
        Lists the current feeds for the current channel or the one provided.
        """

        channel = channel or ctx.channel
        data = await self.config.channel(channel).feeds()

        if not data:
            return await ctx.send(f"{channel}: No feeds.")

        if await ctx.embed_requested():
            output = "\n".join(
                (
                    "{name}: {url} - {posts} posts".format(
                        name=k,
                        url=v.get("url", "broken feed..."),
                        posts=v.get("numberOfPosts", "broken feed...")
                    )
                    for k, v in data.items()
                )
            )
            for page in pagify(output):
                await ctx.send(
                    embed=discord.Embed(
                        description=page, color=(await ctx.embed_color())
                    )
                )
        else:
            output = "\n".join(
                (
                    "{name}: {url} - {posts} posts".format(
                        name=k,
                        url=v.get("url", "broken feed..."),
                        posts=v.get("numberOfPosts", "broken feed...")
                    )
                    for k, v in data.items()
                )
            )
            for page in pagify(output):
                await ctx.send(page)

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
#        await ctx.send("This function is not available yet.")
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
                        feed_settings=feeds[feed],
                        embed_default=should_embed,
                        force=True,
                )
            except Exception as exc:
                debug_exc_log(
                    log,
                    exc,
                    f"Unexpected exception type {type(exc)} encountered for force feed",
                )
                await ctx.send("We caught an error with your request. Try your call again later.")
            else:
                await ctx.tick()
        else:
            await ctx.send("That doesn't appear to be a valid thread.")
