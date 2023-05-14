from .core import ChanFeed
import asyncio
from redbot.core import Config
from redbot.core import commands


async def lets_notify(bot):
    await bot.wait_until_red_ready()
    conf = Config.get_conf(
        None,
        identifier=99123337941934777,
        force_registration=True,
        cog_name="NazuCogs",
    )
    conf.register_global(has_notified=False)

    async with conf.has_notified.get_lock():
        if await conf.has_notified():
            return
        message = (
            "Hello, thank you for installing my ChanFeed Cog."
            "\nPlease understand that this cog may have some issues here and "
            "there. Do not hesitate to report issues and I will try to help."
        )

        await bot.send_to_owners(message)
        await conf.has_notified.set(True)


async def setup(bot: commands.Bot):
    cog = ChanFeed(bot)
    await bot.add_cog(cog)
    cog.initialize()
    #asyncio.create_task(lets_notify(bot))
