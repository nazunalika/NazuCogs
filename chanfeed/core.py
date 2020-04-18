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

# These imports may not be needed
import feedparser

log = logging.getLogger("red.nazucogs.chanfeed")
DONT_HTML_SCRUB = ["link", "source", "updated", "updated_parsed"]

def debug_exc_log(lg: logging.Logger, exc: Exception, msg: str = "Exception in
RSS"):
    if lg.getEffectiveLevel() <= logging.DEBUG:
            lg.exception(msg, exc_info=exc)

class ChanFeed(commands.Cog):
    """
    This is a 4chan feed cog

    This cog has limited support but I will try my best to assist users in this
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

