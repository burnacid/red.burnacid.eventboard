import logging
from typing import Literal, Optional, Union

import re

import contextlib
from datetime import datetime as dt, timezone, timedelta

import discord
from redbot import VersionInfo, version_info
from redbot.core import Config, VersionInfo, checks, commands, version_info
from redbot.core.utils.chat_formatting import humanize_list, pagify
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate

from .helpers import (
    get_event_embed,
    create_event_reactions
)

import asyncio

log = logging.getLogger("red.burnacid.eventboard")

class Eventboard(commands.Cog):
    """Create events within the event channel that members can join or signup to"""

    __version__ = "0.0.1"
    __author__ = "Burnacid"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=144014746356671234)
        default_guild = {
            "event_channel": None,
            "events": {},
            "custom_links": {},
            "next_available_id": 1,
            "auto_end_events": False
        }
        default_user = {"player_class": ""}
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_user)
        self.event_cache = {}
        self.bot.loop.create_task(self.initialize())

    async def initialize(self) -> None:
        if version_info >= VersionInfo.from_str("3.2.0"):
            await self.bot.wait_until_red_ready()
        else:
            await self.bot.wait_until_ready()
        try:
            for guild_id in await self.config.all_guilds():
                guild = self.bot.get_guild(int(guild_id))
                if guild_id not in self.event_cache:
                    self.event_cache[guild_id] = {}
                if guild is None:
                    continue
                data = await self.config.guild(guild).events()
                for user_id, event_data in data.items():
                    try:
                        event = event_data
                    except (TypeError, KeyError, discord.errors.Forbidden):
                        log.error("Error loading events", exc_info=True)
                        continue
                    if event is None:
                        return
                    self.event_cache[guild_id][event["post_id"]] = event
        except Exception as e:
            log.error("Error loading events", exc_info=e)

    def format_help_for_context(self, ctx: commands.Context):
        """
        Thanks Sinbad!
        """
        pre_processed = super().format_help_for_context(ctx)
        return f"{pre_processed}\n\nCog Version: {self.__version__}"

    async def is_mod_or_admin(self, member: discord.Member) -> bool:
        guild = member.guild
        if member == guild.owner:
            return True
        if await self.bot.is_owner(member):
            return True
        if await self.bot.is_admin(member):
            return True
        if await self.bot.is_mod(member):
            return True
        return False
    
    @commands.group(name="eventboard")
    @commands.guild_only()
    async def eventboard(self, ctx: commands.Context):
        """Base command for events"""
        pass

    @eventboard.command(name="create")
    #@allowed_to_create()
    async def event_create(self, ctx: commands.Context):
        """
        Wizard-style event creation tool.
        The event will only be created if all information is provided properly.
        If a minimum required role has been set, users must have that role or
        higher, be in the mod/admin role, or be the guild owner in order to use this command
        """
        author = ctx.author
        guild = ctx.guild
        commandmsg = ctx.message

        def same_author_check(msg):
            return msg.author == author

        if author.dm_channel is None:
            dmchannel = await author.create_dm()
        else:
            dmchannel = author.dm_channel

        # Check if event channel is set
        event_channel = await self.config.guild(guild).event_channel()
        if event_channel is None:
            embed=discord.Embed(title="Error stopping event creation", description="There is no event channel set on the server!", color=0xff0000)
            await dmchannel.send(embed=embed)
            await commandmsg.delete()
            return

        # Get event ID
        event_id = await self.config.guild(guild).next_available_id()
        await self.config.guild(ctx.guild).next_available_id.set(event_id+1)

        # Get event creation time
        creation_time = ctx.message.created_at
        if creation_time.tzinfo is None:
            creation_time = creation_time.replace(tzinfo=timezone.utc).timestamp()
        else:
            creation_time = creation_time.timestamp()

        # Prompt title
        embed=discord.Embed(title="Enter the event title", description="Up to 200 characters are permitted", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check, timeout=300)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            await commandmsg.delete()
            return
        else:
            name = msg.content[0:199]
            if len(name) <= 3:
                embed=discord.Embed(title="Error stopping event creation", description="This title is to short. Try again with atleast 3 characters!", color=0xff0000)
                await dmchannel.send(embed=embed)
                await commandmsg.delete()
                return

        # Prompt Description
        embed=discord.Embed(title="Enter the event description", description="Type `None` for no description. Up to 1600 characters are permitted", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check, timeout=600)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            await commandmsg.delete()
            return
        else:
            description = msg.content[0:1599]
            if description == "none":
                description = None
            
        # max number of attendees
        embed=discord.Embed(title="Enter the maximum number of attendees", description="Type `0` for unlimited number of attendees.", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check, timeout=300)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            await commandmsg.delete()
            return
        else:
            numAttendees = msg.content

        # timezone

        # start time
        embed=discord.Embed(title="When should the event start?", description="Please use `YYYY-MM-DD HH:MM` in 24-hour notation", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check, timeout=300)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            await commandmsg.delete()
            return
        else:
            startDateTimeInput = msg.content
            pattern = re.compile("^[0-9]{4}-(0?[1-9]|1[012])-(0?[1-9]|[12][0-9]|3[01]) (0?[0-9]|1[0-9]|2[0-4]):(0?[0-9]|[1-5][0-9])$")
            
            if not pattern.match(startDateTimeInput):
                embed=discord.Embed(title="Error stopping event creation", description="The date format is not correct!", color=0xff0000)
                await dmchannel.send(embed=embed)
                await commandmsg.delete()
                return

            startDateTime = dt.strptime(startDateTimeInput, '%Y-%m-%d %H:%M')
            if startDateTime < dt.now():
                embed=discord.Embed(title="Error stopping event creation", description="You can't create an event in the past!", color=0xff0000)
                await dmchannel.send(embed=embed)
                await commandmsg.delete()
                return


        # duration

        # repeating

        # Build array
        new_event = {
            "id": event_id,
            "creator": author.id,
            "create_time": creation_time,
            "event_name": name,
            "description": description,
            "max_attendees": numAttendees,
            "event_start": startDateTime.timestamp(),
            "post_id": None
        }

        # Save event and output
        post = await guild.get_channel(event_channel).send(embed=get_event_embed(guild, ctx.message.created_at, new_event))
        new_event["post_id"] = post.id

        async with self.config.guild(guild).events() as event_list:
            event_list[post.id] = new_event

        await create_event_reactions(guild, post)
        self.event_cache[guild.id][post.id] = new_event

        await commandmsg.delete()
    
    @eventboard.command(name="createdebug")
    #@allowed_to_create()
    async def event_createdebug(self, ctx: commands.Context):
        """
        Create standard debug event
        """
        author = ctx.author
        guild = ctx.guild
        commandmsg = ctx.message

        def same_author_check(msg):
            return msg.author == author

        if author.dm_channel is None:
            dmchannel = author.create_dm()
        else:
            dmchannel = author.dm_channel
        
        # Get event creation time
        creation_time = ctx.message.created_at
        if creation_time.tzinfo is None:
            creation_time = creation_time.replace(tzinfo=timezone.utc).timestamp()
        else:
            creation_time = creation_time.timestamp()

        # Check if event channel is set
        event_channel = await self.config.guild(guild).event_channel()
        if event_channel is None:
            embed=discord.Embed(title="Error stopping event creation", description="There is no event channel set on the server!", color=0xff0000)
            await dmchannel.send(embed=embed)
            await commandmsg.delete()
            return

        # Get event ID
        event_id = await self.config.guild(guild).next_available_id()
        await self.config.guild(ctx.guild).next_available_id.set(event_id+1)

        # Build array
        new_event = {
            "id": event_id,
            "creator": author.id,
            "create_time": creation_time,
            "event_name": f"Test event {event_id}",
            "description": "Lorem ipsum dolor sit amet, consectetur adipiscing elit. In eu nibh dui. Integer mauris urna, congue quis iaculis vitae, dapibus eu lectus. Proin efficitur, purus nec varius consectetur, urna lorem vestibulum risus, sed eleifend sem risus sed augue. Sed maximus lacinia mi hendrerit interdum. In est neque, condimentum non malesuada eget, sodales nec mi. Sed convallis augue vel lorem ultrices, sed hendrerit justo blandit. Etiam euismod aliquam eros. Aenean ut justo nec tellus venenatis luctus vel ac diam. Duis tempus metus non aliquam molestie. In vitae velit leo.",
            "max_attendees": "100",
            "event_start": (dt.now() + timedelta(hours=24)).timestamp(),
            "post_id": None
        }

        # Save event and output
        post = await guild.get_channel(event_channel).send(embed=get_event_embed(guild, ctx.message.created_at, new_event))
        new_event["post_id"] = post.id

        async with self.config.guild(guild).events() as event_list:
            event_list[post.id] = new_event

        await create_event_reactions(guild, post)
        self.event_cache[guild.id][post.id] = new_event

        await commandmsg.delete()

    @commands.group(name="eventboardset")
    @commands.guild_only()
    async def eventboard_settings(self, ctx: commands.Context) -> None:
        """Manage server specific settings for events"""
        pass

    @eventboard_settings.command(name="channel")
    @commands.guild_only()
    async def set_guild_eventchannel(self, ctx: commands.Context):
        """
        Set the event channel to the current channel.
        """
        event_channel = await self.config.guild(ctx.guild).event_channel()
        chan = ctx.channel
        if chan and chan.permissions_for(ctx.me).embed_links:
            await self.config.guild(ctx.guild).event_channel.set(chan.id)

            await ctx.send("This channel is now set to Event channel. You can now create events through here by typing `[p]event create`")
            await ctx.message.delete()
        else:
            await ctx.send(
                "I can't set this channel as event channel because I do not have the required permissions"
            )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """
        Checks for reactions to the event
        """
        if payload.member.id == self.bot.user.id:
            return
        if payload.guild_id not in self.event_cache:
            return
        if payload.message_id not in self.event_cache[payload.guild_id]:
            return

        def same_author_check(msg):
            return msg.author == payload.member
                
        if payload.emoji.name == "🗑️":
            event = self.event_cache[payload.guild_id][payload.message_id]
            guild = self.bot.get_guild(int(payload.guild_id))

            if guild is None:
                return
            channel = guild.get_channel(int(payload.channel_id))
            if not channel:
                return
            message = await channel.fetch_message(payload.message_id)
            if not message:
                return

            if payload.member.dm_channel is None:
                dmchannel = await payload.member.create_dm()
            else:
                dmchannel = payload.member.dm_channel

            if payload.member.id != event["creator"] and not await self.is_mod_or_admin(payload.member):            
                await dmchannel.send("Nice try. But that event isn't yours to delete! :-1:")
                await message.remove_reaction(payload.emoji, payload.member)
                
                return

            embed=discord.Embed(title="You like to delete the selected event?", description="Please type `Y` for yes and `N` for no", color=0x0000FF)
            await dmchannel.send(embed=embed)
            try:
                msg = await self.bot.wait_for("message", check=same_author_check, timeout=300)
            except asyncio.TimeoutError:
                await dmchannel.send("I'm not sure where you went. We can try this again later.")
                await message.remove_reaction(payload.emoji, payload.member)
                return
            else:
                if msg.content not in ("y","n"):
                    await dmchannel.send("Just 1 letter is hard I guess. Well suit your self... I won't delete anything then.")
                    await message.remove_reaction(payload.emoji, payload.member)
                    return

                if msg.content == "n":
                    await message.remove_reaction(payload.emoji, payload.member)
                    await dmchannel.send("Canceled!")
                    return

                deletemsg = await message.delete()
                if deletemsg is None:
                    async with self.config.guild(guild).events() as event_list:
                        del event_list[str(payload.message_id)]
                        del self.event_cache[guild.id][payload.message_id]
                        await dmchannel.send("And it's gone...")
            
        else:
            log.debug("No matching reaction")

        return


    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """
        Checks for reactions to the event
        """
        log.debug("Test Remove")
        return

    @commands.Cog.listener()
    async def on_message(self, message):
        """
        Checks for messages in event channel
        """

        if message.guild is None:
            return
        if message.guild.id not in self.event_cache:
            return
        if message.author.id == self.bot.user.id:
            return

        channel = message.channel
        event_channel = await self.config.guild(message.guild).event_channel()

        if event_channel is None:
            return
        if event_channel != channel.id:
            return
        
        if not message.content[1:].startswith("eventboard"):
            msg = await message.channel.send("Please don't chat in the event channel")
            await msg.delete(delay=10)

        await message.delete(delay=10)