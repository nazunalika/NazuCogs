from .core import RoleManagement

__red_end_user_data_statement__ = "This will only store birthdays, sticky, and subscribed roles for users."

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
            "Hello, thank you for installing the rolemanagement cog."
            "\nPlease understand that this cog is simply a fork. If there are"
            " issues, do not hesitate to report them and I will try to help."
        )

        await bot.send_to_owners(message)
        await conf.has_notified.set(True)



async def setup(bot):
    cog = RoleManagement(bot)
    await bot.add_cog(cog)
    cog.init()
