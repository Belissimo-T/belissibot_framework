import asyncio
import inspect
import random
import sys
import traceback
from asyncio import Event
from typing import Awaitable, Callable, Optional, Union
import ast

import context_logger
import discord
from context_logger import Logger, log, BaseIndent, STD_SPACE_INDENT


def contruct_log_embed(log_: list[str]):
    logstr = "\n".join(log_)
    return discord.Embed(title="Log", description=f"```{logstr} ```")


class Log:
    log_list: list[str]
    log_message: discord.Message
    event: Event

    @classmethod
    async def create(cls, message: discord.Message):
        self = cls()
        self.message = message
        self.log_list = [" "]
        self.loop = True

        self.log_message = await message.channel.send(embed=self.get_log_embed(), reference=message)

        self.event = Event()

        asyncio.create_task(self.mainloop())

        return self

    def get_log_embed(self):
        return contruct_log_embed(self.log_list)

    def log(self, message_str: str, _, prefix: str, nlist: list[int], indent: BaseIndent):
        msg = f"{prefix}: {indent(nlist)}{message_str}"
        self.msg(msg)

    def msg(self, msg: str):
        self.log_list.append(msg)
        self.event.set()

    async def safe_edit(self, **kwargs):
        try:
            await self.log_message.edit(**kwargs)
        except discord.HTTPException:
            self.log_list = ["LOG TOO LONG FOR DISCORD :("]
            self.event.set()

    async def mainloop(self):
        while self.loop:
            await self.event.wait()
            await self.safe_edit(embed=self.get_log_embed())

    async def close(self, delete_after: int = 2 * 60):
        self.loop = False
        self.msg("END")
        await self.safe_edit(content=f"Gets auto deleted after {delete_after} s.", delete_after=delete_after,
                             embed=self.get_log_embed())


def construct_unauthorized_embed(unauthorized_user: discord.User):
    return discord.Embed(title="Unauthorized", color=discord.Color(0xFFA000),
                         description=f"You ({unauthorized_user}) are unathorized to perform this action.")


class BotError(Exception):
    def __init__(self, message: str, embed: discord.Embed | None = None):
        self.message = message
        self.embed = embed


def construct_bot_error_embed(err: BotError):
    if err.embed:
        return err.embed

    titles = ["It's your fault.", "The mistake sits in front of the screen.", "You messed up, here's why.",
              "One of us is smarter than you.", "You F'd up.", "You did no-no.", "Error", "You did something wrong."]

    emojis = ["🙈🙉🙊", "🙈🙉", "🙈🙊", "🙉🙊", *"🥸🤡😬🤨🙁🫣🤦🤷️🧐🙅🙈🙉🙊"]

    return discord.Embed(title=random.choice(titles) + " " + random.choice(emojis), description=err.message,
                         color=discord.Color(0xFF0000))


def construct_error_embed(err: str):
    # BTW, https://en.wikipedia.org/wiki/Minced_oath
    messages = ["Snap", "Shoot", "Shucks", "Shizer", "Darn", "Frick", "Juck", "Dang", "Frack", "Frak",
                "Frig", "Fug", "F", "my gosh"]
    return discord.Embed(title="Error",
                         description=f"{random.choice(['Oh ', 'Aw ', 'Oh My '])}{random.choice(messages)}! Something "
                                     f"went wrong:\n```{err}```"
                                     f"Don't be scared to read the error, most are simple mistakes and can be easily "
                                     f"resolved! 🧐. Sometimes, trying again 🔁 helps! Also make sure to not run "
                                     f"things in parallel.",
                         color=discord.Color(0xFF0000))


def construct_help_embed(command: str, description: str, example_: str, argstr: str = None,
                         **args: Union[tuple[str, str], str]):
    argstr = ' '.join([
        (f'<{arg}: {desc_or_type[1]}>' if isinstance(desc_or_type, tuple) else f'<{arg}>')
        for arg, desc_or_type in args.items()
    ]) if argstr is None else argstr

    out = discord.Embed(title=f"Usage of `{command}`",
                        description=f"{description}\n\n"
                                    f"__Usage:__ ```\n{command} {argstr}```\n"
                                    f"__Example:__```\n{example_}```",
                        color=discord.Color(0xFFFF00))

    for arg, desc in args.items():
        if isinstance(desc, tuple):
            desc, _ = desc

        out.add_field(name=arg, value=desc)

    # out.set_footer(text="This help embed was created using the construct_help_embed function.")

    return out


def parse_py_args(_message: str):
    args = []

    message = _message.split()

    i = 0
    while i < len(message):
        # noinspection PyBroadException
        try:
            arg = ast.literal_eval(" ".join(message[:i + 1]))

            args.append(arg)
            del message[:i + 1]
            i = 0
        except Exception:
            print(message[:i])
            i += 1
    return args


def change_corofuncname_to_on_message(corofunc):
    async def on_message(*args, **kwargs):
        return await corofunc(*args, **kwargs)

    return on_message


class App:
    def __init__(self):
        self.commands: dict[str: Awaitable] = {}
        self.message_number = 0
        self.on_messages = []

    def on_message(self):
        def decorator(func):
            self.on_messages.append(func)

            async def wrapper(client: discord.Client, message: discord.Message):
                await func(client, message)

            return wrapper

        return decorator

    def route(self, alias: str, *, only_from_users: list[int] = None, only_from_roles: list[int] = None,
              do_log: bool = False, print_unauthorized: bool = False, raw_args: bool = False, typing: bool = True,
              member_arg: bool = False, only_on_servers: bool = False, delete_message: bool = True):
        if not only_on_servers and (only_from_roles or member_arg):
            raise Exception("Invalid argument combination: only_on_servers needs to be activated in order to be able"
                            "to use only_from_roles or member_arg since these features only make sense on a server.")

        only_from_roles = None if only_from_roles is None else set(only_from_roles)

        def decorator(func: Callable):
            @context_logger.async_safe
            async def wrapper(client: discord.Client, message: discord.Message, end: int):
                if message.guild is None and only_on_servers:
                    return

                # noinspection PyTypeChecker
                member = None
                member_arg_list = []
                if only_from_roles or member_arg:
                    member: discord.Member = message.guild.get_member(message.author.id)

                    if member is None:
                        await message.channel.send(
                            embed=discord.Embed(title="Not enough permissions",
                                                description=f"Couldn't fetch member {message.author} with id "
                                                            f"`{message.author.id}`",
                                                color=discord.Color(0xFF0000)))
                        return

                    if member_arg:
                        member_arg_list = [member]

                if ((only_from_users and (message.author.id not in only_from_users)) or
                    not (only_from_roles and ({role.id for role in member.roles} & only_from_roles))) \
                        and print_unauthorized:
                    log(f"Unauthorized: {only_from_users!r}, {only_from_roles!r}, {member.roles!r}")
                    await message.channel.send(embed=construct_unauthorized_embed(message.author),
                                               reference=message, delete_after=30)
                    return

                if not raw_args:
                    args = parse_py_args(message.content[end:])
                    log(f"Parsed args: {args!r}")
                else:
                    args = [message.content[end:]]

                kwargs = {}

                typing_ctxtmgr = message.channel.typing()

                if typing:
                    await typing_ctxtmgr.__aenter__()
                try:
                    if do_log:
                        log_object = await Log.create(message)
                        try:
                            with Logger(
                                    f"{self.message_number}",
                                    log_function=context_logger.both(log_object.log, context_logger.std_log_function),
                                    indent=STD_SPACE_INDENT):
                                await func(client, message, *member_arg_list, *args, **kwargs)
                        finally:
                            await log_object.close()
                    else:
                        @context_logger.async_safe
                        async def run_func():
                            with Logger(f"MSG {self.message_number}"):
                                await func(client, message, *member_arg_list, *args, **kwargs)

                        await run_func()
                finally:
                    if typing:
                        await typing_ctxtmgr.__aexit__(None, None, None)

                if delete_message:
                    try:
                        await message.delete()
                    except (discord.NotFound, discord.Forbidden):
                        # message was already deleted by func or we're in a DM channel, or we just aren't allowed to
                        # delete
                        ...

            self.commands.update({alias: wrapper})
            return func

        return decorator

    def add_help(self, command: str, description: str, example_: str, argstr: str = None, route_kwargs: dict = None,
                 send_kwargs: dict = None, **arg_descriptions):
        route_kwargs = {"raw_args": True} if route_kwargs is None else route_kwargs
        send_kwargs = {} if send_kwargs is None else send_kwargs

        def decorator(func):
            @self.route(f"{command} help", **route_kwargs)
            async def help_func(client: discord.Client, message: discord.Message, _=""):
                _, _, _, _, _, _, arg_annotations = inspect.getfullargspec(func)

                await message.channel.send(
                    embed=construct_help_embed(
                        command=command,
                        description=description,
                        example_=example_,
                        argstr=argstr,
                        **{arg: (arg_desc, arg_annotations[arg].__name__) if arg in arg_annotations else arg_desc
                           for arg, arg_desc in arg_descriptions.items()}),
                    **send_kwargs
                )

            return func

        return decorator

    def run(self, discord_token, game: str = None, intents: discord.Intents = None):
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            log(f'{client.user} has connected to Discord! Bot is part of {len(client.guilds)} guilds.')
            if game:
                await client.change_presence(activity=discord.Game(name=game))

        @client.event
        @change_corofuncname_to_on_message
        @context_logger.async_safe
        async def on_message(message: discord.Message):
            async def on_messages_coro():
                await asyncio.gather(*[func(client, message) for func in self.on_messages])

            asyncio.create_task(on_messages_coro())

            await self.invoke(message, client)

        client.run(discord_token)

    @context_logger.safe
    async def invoke(self, message, client):
        record_alias: Optional[str] = None
        for alias in self.commands:
            if message.content.startswith(alias) and (not record_alias or len(alias) > len(record_alias)):
                record_alias = alias
        if record_alias is None:
            return
        self.message_number += 1

        end = len(record_alias) + 1

        log(f"Relevant message recieved: {message.content!r}:")
        log(f"Decided on {message.content[:end]!r}, argstr is {message.content[end:]!r}")

        try:
            with log("Running wrapper"):
                await self.commands[record_alias](client, message, end)
            log("Finished!")

        except BotError as e:
            await message.channel.send(embed=construct_bot_error_embed(e))

        except Exception:
            err = traceback.format_exc()
            sys.stderr.write(err)
            await message.channel.send(embed=construct_error_embed(err))
