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
    create_event_reactions,
    valid_image
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
            "auto_end_events": False,
            "autodelete": 60
        }
        default_user = {"player_class": ""}
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_user)
        self.event_cache = {}
        self.event_init_task = self.bot.loop.create_task(self.initialize())
        self.event_maintenance = self.bot.loop.create_task(self.maintenance_events())

        self.reactionEmoji = {"attending": "✅", "declined": "❌", "maybe": "❔"}

    def cog_unload(self):
        self.event_init_task.cancel()
        self.event_maintenance.cancel()

    async def initialize(self) -> None:
        CHECK_DELAY = 300
        while self == self.bot.get_cog("Eventboard"):
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
                    for post_id, event_data in data.items():
                        try:
                            event = event_data
                        except (TypeError, KeyError, discord.errors.Forbidden):
                            log.error("Error loading events", exc_info=True)
                            continue
                        if event is None:
                            return
                        self.event_cache[guild_id][post_id] = event
            except Exception as e:
                log.error("Error loading events", exc_info=e)

            await asyncio.sleep(CHECK_DELAY)

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
            return
        else:
            name = msg.content[0:199]
            if len(name) <= 3:
                embed=discord.Embed(title="Error stopping event creation", description="This title is to short. Try again with atleast 3 characters!", color=0xff0000)
                await dmchannel.send(embed=embed)
                return

        # Prompt Description
        embed=discord.Embed(title="Enter the event description", description="Type `None` for no description. Up to 1600 characters are permitted", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check, timeout=600)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            return
        else:
            description = msg.content[0:1599]
            if description.lower() == "none":
                description = None
            
        # max number of attendees
        embed=discord.Embed(title="Enter the maximum number of attendees", description="Type `0` for unlimited number of attendees.", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check, timeout=300)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
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
            return
        else:
            startDateTimeInput = msg.content
            pattern = re.compile("^[0-9]{4}-(0?[1-9]|1[012])-(0?[1-9]|[12][0-9]|3[01]) (0?[0-9]|1[0-9]|2[0-4]):(0?[0-9]|[1-5][0-9])$")
            
            if not pattern.match(startDateTimeInput):
                embed=discord.Embed(title="Error stopping event creation", description="The date format is not correct!", color=0xff0000)
                await dmchannel.send(embed=embed)
                return

            startDateTime = dt.strptime(startDateTimeInput, '%Y-%m-%d %H:%M')
            if startDateTime < dt.now():
                embed=discord.Embed(title="Error stopping event creation", description="You can't create an event in the past!", color=0xff0000)
                await dmchannel.send(embed=embed)
                return


        # duration

        # repeating

        # Image
        embed=discord.Embed(title="Would you like to add an event image?", description="Type `None` for no image. Please write an URL of an image. Must be a HTTPS url.", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check, timeout=300)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            return
        else:
            image = msg.content
            if image.lower() == "none":
                image = None
            else:    
                if not await valid_image(image):
                    embed=discord.Embed(title="Error stopping event creation", description="That URL doesn't look like a proper image.", color=0xff0000)
                    await dmchannel.send(embed=embed)
                    return

        # Build array
        new_event = {
            "id": event_id,
            "creator": author.id,
            "create_time": creation_time,
            "event_name": name,
            "description": description,
            "max_attendees": numAttendees,
            "event_start": startDateTime.timestamp(),
            "post_id": None,
            "attending": {},
            "declined": {},
            "maybe": {},
            "image": image
        }

        # Save event and output
        post = await guild.get_channel(event_channel).send(embed=get_event_embed(guild, new_event))
        new_event["post_id"] = post.id

        async with self.config.guild(guild).events() as event_list:
            event_list[post.id] = new_event

        await create_event_reactions(guild, post)
        if guild.id not in self.event_cache:
            self.event_cache[guild.id] = {}
        self.event_cache[guild.id][str(post.id)] = new_event
    
    @eventboard.command(name="createdebug")
    #@allowed_to_create()
    async def event_createdebug(self, ctx: commands.Context):
        """
        Create standard debug event
        """
        author = ctx.author
        guild = ctx.guild
        commandmsg = ctx.message

        if author.dm_channel is None:
            dmchannel = await author.create_dm()
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
            "max_attendees": "0",
            "event_start": (dt.now() + timedelta(minutes=10)).timestamp(),
            "post_id": None,
            "attending": {},
            "declined": {},
            "maybe": {},
            "image": "https://media.sproutsocial.com/uploads/2017/02/10x-featured-social-media-image-size.png"
        }

        # Save event and output
        post = await guild.get_channel(event_channel).send(embed=get_event_embed(guild, new_event))
        new_event["post_id"] = post.id

        async with self.config.guild(guild).events() as event_list:
            event_list[post.id] = new_event

        await create_event_reactions(guild, post)
        self.event_cache[guild.id][str(post.id)] = new_event

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
        if chan.id == event_channel:
            await self.config.guild(ctx.guild).event_channel.set(None)
            await ctx.send("This channel is no longer marked as eventchannel!")
            pins = await chan.pins()
            for pinned_message in pins:
                if pinned_message.author.id == self.bot.user.id:
                    await pinned_message.delete()
            return

        if chan and chan.permissions_for(ctx.me).embed_links:
            await self.config.guild(ctx.guild).event_channel.set(chan.id)

            pin = await ctx.send(f"This channel is now set to Event channel. You can now create events through here by typing `{ctx.clean_prefix}eventboard create`")
            await pin.pin()
            await ctx.message.delete()
        else:
            await ctx.send(
                "I can't set this channel as event channel because I do not have the required permissions"
            )
    
    @eventboard_settings.command(name="autodelete")
    @commands.guild_only()
    async def set_guild_autodelete(self, ctx: commands.Context, *, minutes: int):
        """
        Set how long after start time the events post will be deleted

        `{minutes}` the number of minutes after which the eventpost is removed after the start time. Set to -1 to disable removal of the events
        """

        await self.config.guild(ctx.guild).autodelete.set(int(minutes))
        await ctx.message.delete()
        if minutes < 0:
            await ctx.channel.send("Auto delete events is disabled", delete_after=60)
        else:
            await ctx.channel.send(f"Event messages will now be deleted {minutes} minutes after start time", delete_after=60)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """
        Checks for reactions to the event
        """

        if payload.member.id == self.bot.user.id:
            return
        if payload.guild_id not in self.event_cache:
            return
        if str(payload.message_id) not in self.event_cache[payload.guild_id]:
            return

        def same_author_check(msg):
            return msg.author == payload.member
                
        if payload.emoji.name == "🗑️":
            event = self.event_cache[payload.guild_id][str(payload.message_id)]
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
                        del self.event_cache[guild.id][str(payload.message_id)]
                        await dmchannel.send("And it's gone...")
            return
        
        if payload.emoji.name in ("✅","❌","❔"):
            event = self.event_cache[payload.guild_id][str(payload.message_id)]
            guild = self.bot.get_guild(int(payload.guild_id))

            channel = guild.get_channel(int(payload.channel_id))
            if not channel:
                return

            message = await channel.fetch_message(payload.message_id)
            
            if payload.emoji.name == "✅":
                async with self.config.guild(guild).events() as events_list:
                    event = self.event_cache[payload.guild_id][str(payload.message_id)]
                    num_addending = len(event['attending'])
                    if int(event["max_attendees"]) <= int(num_addending) and int(event["max_attendees"]) != 0:
                        await channel.send(f"Sorry {payload.member.mention} this event is full.", delete_after=30)
                        await message.remove_reaction(payload.emoji, payload.member)
                        return

                    self.event_cache[payload.guild_id][str(payload.message_id)]["attending"][payload.member.id] = payload.member.id
                    clean = {"declined","maybe"}

            if payload.emoji.name == "❌":
                async with self.config.guild(guild).events() as events_list:
                    self.event_cache[payload.guild_id][str(payload.message_id)]["declined"][payload.member.id] = payload.member.id
                    clean = {"attending","maybe"}

            if payload.emoji.name == "❔":
                async with self.config.guild(guild).events() as events_list:
                    self.event_cache[payload.guild_id][str(payload.message_id)]["maybe"][payload.member.id] = payload.member.id
                    clean = {"attending","declined"}

            for reactionClean in clean:
                async with self.config.guild(guild).events() as events_list:
                    if payload.user_id in self.event_cache[payload.guild_id][str(payload.message_id)][reactionClean]:
                        del self.event_cache[payload.guild_id][str(payload.message_id)][reactionClean][payload.user_id]
                        events_list[str(payload.message_id)] = self.event_cache[payload.guild_id][str(payload.message_id)]
                        await message.remove_reaction(self.reactionEmoji[reactionClean], payload.member)

            events_list[str(payload.message_id)] = self.event_cache[payload.guild_id][str(payload.message_id)]            
            updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]
            embed = get_event_embed(guild=guild,event=updated_event)
            await message.edit(embed=embed, suppress=False)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """
        Checks for reactions to the event
        """

        if payload.user_id == self.bot.user.id:
            return 
        if payload.guild_id not in self.event_cache:
            return
        if str(payload.message_id) not in self.event_cache[payload.guild_id]:
            return

        if payload.emoji.name in ("✅","❌","❔"):
            guild = self.bot.get_guild(int(payload.guild_id))
            channel = guild.get_channel(int(payload.channel_id))
            if not channel:
                return

            message = await channel.fetch_message(payload.message_id)
            if not message:
                return
                
            if payload.emoji.name == "✅":
                async with self.config.guild(guild).events() as events_list:
                    if payload.user_id not in self.event_cache[payload.guild_id][str(payload.message_id)]["attending"]:
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]
                    else:
                        del self.event_cache[payload.guild_id][str(payload.message_id)]["attending"][payload.user_id]
                        events_list[str(payload.message_id)] = self.event_cache[payload.guild_id][str(payload.message_id)]
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]

                    embed = get_event_embed(guild=guild,event=updated_event)
                    await message.edit(embed=embed, suppress=False)
                    return

            if payload.emoji.name == "❌":
                async with self.config.guild(guild).events() as events_list:
                    if payload.user_id not in self.event_cache[payload.guild_id][str(payload.message_id)]["declined"]:
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]
                    else:
                        del self.event_cache[payload.guild_id][str(payload.message_id)]["declined"][payload.user_id]
                        events_list[str(payload.message_id)] = self.event_cache[payload.guild_id][str(payload.message_id)]
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]

                    embed = get_event_embed(guild=guild,event=updated_event)
                    await message.edit(embed=embed, suppress=False)
                    return

            if payload.emoji.name == "❔":
                async with self.config.guild(guild).events() as events_list:
                    if payload.user_id not in self.event_cache[payload.guild_id][str(payload.message_id)]["maybe"]:
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]
                    else:
                        del self.event_cache[payload.guild_id][str(payload.message_id)]["maybe"][payload.user_id]
                        events_list[str(payload.message_id)] = self.event_cache[payload.guild_id][str(payload.message_id)]
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]

                    embed = get_event_embed(guild=guild,event=updated_event)
                    await message.edit(embed=embed, suppress=False)
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
    
    async def maintenance_events(self) -> None:
        CHECK_DELAY = 60
        while self == self.bot.get_cog("Eventboard"):
            for guild_id in await self.config.all_guilds():
                guild = self.bot.get_guild(int(guild_id))
                if guild_id not in self.event_cache:
                    continue
                if guild is None:
                    continue
                event_channel = event_channel = await self.config.guild(guild).event_channel()
                if event_channel is None:
                    continue
                channel = guild.get_channel(event_channel)
                if channel is None:
                    continue

                data = await self.config.guild(guild).events()
                for post_id, event_data in data.items():
                    event = event_data
                    try:
                        message = await channel.fetch_message(int(post_id))
                    except discord.NotFound:
                        if event["event_start"] < (dt.now()).timestamp():
                            # Delete historic message
                            async with self.config.guild(guild).events() as event_list:
                                del event_list[str(post_id)]
                                del self.event_cache[guild.id][str(post_id)]
                        else:
                            # Recreate message
                            post = await guild.get_channel(event_channel).send(embed=get_event_embed(guild, event))
                            event["post_id"] = post.id

                            async with self.config.guild(guild).events() as event_list:
                                event_list[post.id] = event
                                self.event_cache[guild.id][str(post.id)] = event
                                del event_list[str(post_id)]
                                del self.event_cache[guild.id][str(post_id)]

                            await create_event_reactions(guild, post)

                        continue
                    
                    autodelete = int(await self.config.guild(guild).autodelete())
                    if autodelete >= 0:
                        autodelete = autodelete * -1
                        if event["event_start"] < (dt.now() + timedelta(minutes=autodelete)).timestamp():
                            # Event has started and can be deleted
                            deletemsg = await message.delete()
                            if deletemsg is None:
                                async with self.config.guild(guild).events() as event_list:
                                    del event_list[str(post_id)]
                                    del self.event_cache[guild.id][str(post_id)]
                            continue

                    if len(message.embeds) == 0:
                        #Embed is removed. Recreate
                        embed = get_event_embed(guild=guild,event=event)
                        await message.edit(embed=embed, suppress=False)

            await asyncio.sleep(CHECK_DELAY)
