from __future__ import annotations

import asyncio
import logging
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
from .converters import TriState

log = logging.getLogger("red.nazucogs.chanfeed")
log.setLevel(logging.DEBUG)

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
    __version__ = "330.0.3"

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
    def check_thread_archival(r):
        return True

    @staticmethod
    def url_splitter(data):
        url_split = data.rsplit('/', 3)
        output = {}
        output['board'] = url_split[1]
        output['thread'] = url_split[3]
        return output

    @staticmethod
    def check_thread_replies(r):
        if len(response.replies) == 0:
            thread_reply_number = 0
            last_reply = response.topic
        else:
            thread_reply_number = len(response.replies) - 1
            last_reply = response.replies[thread_reply_number]

        output = {}
        output['thread_reply_number'] = thread_reply_number
        output['last_reply'] = last_reply

        return output

    @staticmethod
    def thread_is_archived(t):
        archived_comment = "Thread (%s) is archived" % (t)
        return {"content": archived_comment, "embed": None}

    # fetch the feed here
    # Check that the board exists and then check the thread exists
    async def fetch_feed(self, url: str):
        timeout = aiohttp.client.ClientTimeout(total=15)
        # SPLIT OUT THE URL HERE
        split = self.url_splitter(url)
        url_generation = 'https://a.4cdn.org/' + split['board'] + '/thread/' + split['thread'] + '.json'
        try:
            async with self.session.get(url_generation, timeout=timeout) as response:
                data = await response.read()

            chanboard = basc_py4chan.Board(split['board'])
            chanthread = chanboard.get_thread(split['thread'])
            if chanboard.title is not None:
                if chanthread.id is not None:
                    pass
                if chanthread.archived:
                    # The thread is archived, so don't add.
                    #log.debug(f"The thread is archived and is not considered valid.")
                    # I guess I won't really need this, since the archive
                    # status is actually checked later. I'll remove this
                    # section in a later release.
                    pass

        except (aiohttp.ClientError, asyncio.TimeoutError):
            # We couldn't connect
            log.debug(f"We could not connect to 4chan.org")
            debug_exc_log(
                log,
                exc,
                f"We could not connect to 4chan.org.",
            )
            return None
        except KeyError:
            # The board doesn't exist
            log.debug(f"The specified board {board} does not exist")
            debug_exc_log(
                log,
                exc,
                f"The specified board {board} does not exist.",
            )
            return None
        except AttributeError:
            # The thread doesn't exist
            log.debug(f"The specified thread {thread} does not exist")
            debug_exc_log(
                log,
                exc,
                f"The specified thread {thread} does not exist.",
            )
            return None
        except Exception as exc:
            debug_exc_log(
                log,
                exc,
                f"Unexpected exception type {type(exc)} encountered for {board} -> {thread}",
            )
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
        last_current_post = int(feed_settings.get("lastPostID", None))
        thread_reply_number = int(feed_settings.get("numberOfPosts", None))
        new_replies = len(response.replies) - 1
        if response.last_reply_id > last_current_post:
            loopydata['entries'] = []
            if new_replies > thread_reply_number:
                howmany = [i for i in range(thread_reply_number - new_replies, 0)]
                for k in howmany:
                    loopydata['entries'].append(response.replies[k])
            elif new_replies == thread_reply_number:
                loopydata['entries'].append(response.replies[-1])
        elif force:
            loopydata['entries'] = []
            if thread_reply_number == 0:
                loopydata['entries'].append(response.op)
            else:
                loopydata['entries'].append(response.replies[-1])
        else:
            return None

        assert isinstance(loopydata, dict), "mypy"
        assert isinstance(loopydata['entries'], list), "mypy"

        to_send = loopydata['entries']

        # We just want to force out the last post here, we don't care about the
        # other posts
        if force:
            if response.replies != 0:
                howmany = [i for i in range(thread_reply_number - new_replies, -1)]
                for k in howmany:
                    del loopydata['entries'][k]

        # Format the post and then try to send it out
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
                log.error(exc)
            last_sent = {
                'timestamp': list(self.process_entry_timestamp(entry)),
                'postnumber': str(entry.number),
                'posts': str(new_replies)
            }

        # We should send a message here if the thread is detected as archived

        return last_sent

    def format_post(
            self,
            entry,
            embed: bool,
            color,
    ) -> dict:

        # ERROR HANDLING PLEASE
        # CHOOSE A BETTER NAME MAYBE
        reply = entry
        #board = self.url_splitter(reply.url)['board']
        #current_thread = re.sub('#p\d+', '', self.url_splitter(reply.url)['thread'])
        # create vars for all relevant pieces of the embed
        chan_logo_img = "https://i.imgur.com/qwj5bL2.png"
        post_timestamp = time.strftime('%m/%d/%y (%a) %H:%M:%S', time.localtime(reply.timestamp))
        post_url = reply.url
        poster_id = reply.number
        poster_name = reply.name
        poster = reply.poster_id or ""
        poster_trip = reply.tripcode or ""
        clear_comment = reply.text_comment
        thumbnail_url = reply.file_url
        #thumbnail_url = reply.thumbnail_url
        #thread_url = 'https://boards.4chan.org/%s/thread/%s' % (board, current_thread)

        # Convert the reply.url to https while removing the #p... reply - I
        # can't think of a better way of doing this for creating reply links in
        # the embeds
        thread_url = re.sub(
            r'http:\/\/boards\.4chan\.org\/([a-z0-9]+)\/thread\/(\d+)(#p\d+)',
            r'https://boards.4chan.org/\1/thread/\2',
            reply.url
        )
        content = re.sub(r'>{2}(\d+)', r'[>>\1](' + thread_url + r'#p\1)', clear_comment)
        # I want a better way of handling these at some point, but this is the
        # closest I could get to for cross-linking. It may or may not be
        # consistent. I may want to try conditionals since python supports it.

        # Search for a cross-link via >>>/.../, negating any digits via
        # lookahead after the final /
        content = re.sub(r'>{3}(/[a-z0-9]+/(?!\d+))', r'[>>>\1](https://boards.4chan.org\1)', content)

        # Search for cross-lin via >>>/.../..., accepting any digits after the
        # final / as another capture group
        content = re.sub(r'>{3}(/[a-z0-9]+/)(\d+)', r'[>>>\1\2](https://boards.4chan.org\1thread/\2)', content)
        embed_title = "%s %s %s" % (poster_name, poster_trip, poster)
        embed_desc = "[No. %s](%s)\r\r%s" % (poster_id, post_url, content)

        # Prepare the data that is to be sent, either in an embed or plain text
        if embed:
            if len(content) > 2000:
                content = content[:1999] + "... (post is too long)"

            timestamp = datetime(*self.process_entry_timestamp(reply))
            embed_data = discord.Embed(
                description=embed_desc, color=color, timestamp=timestamp
            )
            embed_data.set_author(name=embed_title, icon_url=chan_logo_img)
            embed_data.set_footer(text="Timestamp: ")

            if thumbnail_url:
                embed_data.set_image(url=thumbnail_url)

            return {"content": None, "embed": embed_data}
        else:
            if len(content) > 2000:
                clear_comment = clear_comment[:1900] + "... (post is too long)"

            return {"content": clear_comment, "embed": None}

    async def handle_response_from_loop(
            self,
            *,
            response,
            channel: discord.TextChannel,
            feed: dict,
            feed_name: str,
            should_embed: bool,
    ):
        # If the response is none, just skip. Otherwise, we're going to go
        # ahead and try format and send the message. If anything changed or a
        # post was provided, we'll update our configuration.
        if not response:
            return
        elif response.archived:
            await self.config.channel(channel).feeds.set_raw(
                feed_name, "isArchived", value=response.archived
            )
            archivepost = thread_is_archived(feed_name)
            await self.bot.send_filtered(channel, **archivepost)
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

                await self.config.channel(channel).feeds.set_raw(
                    feed_name, "numberOfImages", value=response.num_images
                )

                await self.config.channel(channel).feeds.set_raw(
                    feed_name, "isArchived", value=response.archived
                )

                await self.config.channel(channel).feeds.set_raw(
                    feed_name, "isSticky", value=response.sticky
                )

                await self.config.channel(channel).feeds.set_raw(
                    feed_name, "isAtBumpLimit", value=response.bumplimit
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
                archived = feed.get("isArchived")
                if not url:
                    continue
                if archived:
                    pass
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
                    f"That doesn't appear to be a valid thread. "
                    f"Thread is either archived, the board/thread does not exist, "
                    f"or we could not connect to 4chan.org."
                    f"\n\nCheck the bot logs for more information."
                )

            elif response.archived:
                return await ctx.send(
                    f"That thread is not valid because it is archived. "
                    f"\n\nCheck the bot logs for more information."
                )

            else:
                # We don't know if .posts or .all_posts would be appropriate
                # I don't seem to understand what they mean by "omitted" in the
                # 4chan API documentation. So we're using .replies instead for
                # now. https://github.com/nazunalika/NazuCogs/issues/6
                if len(response.replies) == 0:
                    thread_reply_number = 0
                    last_reply = response.topic
                else:
                    thread_reply_number = len(response.replies) - 1
                    last_reply = response.replies[thread_reply_number]

                last_timestamp = list(tuple((time.gmtime(last_reply.timestamp) or (0,)))[:7])

                feeds.update(
                    {
                        name: {
                            "url": url,
                            "embed_override": None,
                            "lastPostID": response.last_reply_id,
                            "numberOfPosts": thread_reply_number,
                            "lastPostTimestamp": last_timestamp,
                            "numberOfImages": response.num_images,
                            "isArchived": response.archived,
                            "isSticky": response.sticky,
                            "isAtBumpLimit": response.bumplimit,
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

    @chanfeed.command(name="stats")
    async def stats_feeds(
            self,
            ctx: commands.GuildContext,
            feed,
            channel: Optional[discord.TextChannel] = None
    ):
        """
        Displays the stats for a particular thread feed
        """

        channel = channel or ctx.channel
        feeds = await self.config.channel(channel).feeds()
        data = await self.config.channel(channel).feeds()
        url = None

        if feed in feeds:
            url = feeds[feed].get("url", None)

        if url is None:
            return await ctx.send("There is no such feed available. Try your call again later.")

        if not data:
            return await ctx.send(f"{channel}: No feeds.")

        response = await self.fetch_feed(url)

        if await ctx.embed_requested():
            output = "\n".join(
                (
                    "**{name}: {url}**\n**Replies**: {posts}\n**Images**: {images}\n**Archived**: {archived}\n**Sticky**: {sticky}\n**Bump Limit Reached**: {bumplimit}".format(
                        name=k,
                        url=v.get("url", "broken feed..."),
                        posts=v.get("numberOfPosts", "broken feed..."),
                        images=v.get("numberOfImages", "broken feed..."),
                        archived=v.get("isArchived", "broken feed..."),
                        sticky=v.get("isSticky", "broken feed..."),
                        bumplimit=v.get("isAtBumpLimit", "broken feed...")
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
                    "{name}: {url}\nReplies: {posts}\nImages: {images}\nArchived: {archived}\nSticky: {sticky}\nBump Limit Reached: {bumplimit}".format(
                        name=k,
                        url=v.get("url", "broken feed..."),
                        posts=v.get("numberOfPosts", "broken feed..."),
                        images=v.get("numberOfImages", "broken feed..."),
                        archived=v.get("isArchived", "broken feed..."),
                        sticky=v.get("isSticky", "broken feed..."),
                        bumplimit=v.get("isAtBumpLimit", "broken feed...")
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
                log.debug(exc)
                await ctx.send(f"We caught an error with your request. Try your call again later.\rMessage: {exc}")
            else:
                await ctx.tick()
        else:
            await ctx.send("That doesn't appear to be a valid thread.")

