import logging
from typing import Literal, Optional, Union
import copy

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
    valid_image,
    get_mentionable_role,
    get_role_mention
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
            "autodelete": 60,
            "reminder": -1,
            "mentions": {},
            "mention_all": 1
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
            log.debug("Running Event Init")
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

            log.debug("Ended Event Init")
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

    @eventboard.group(name="manage")
    @commands.guild_only()
    async def eventboard_manage(self, ctx: commands.Context):
        """Manage your events"""
        pass

    @eventboard_manage.command("addattending")
    @commands.guild_only()
    async def eventboard_addattending(self, ctx: commands.Context):
        """Manually add an attending member"""
        author = ctx.author
        guild = ctx.guild

        if author.dm_channel is None:
            dmchannel = await author.create_dm()
        else:
            dmchannel = author.dm_channel

        def same_author_check_dm(msg):
            return msg.author == author and msg.channel == dmchannel

        await ctx.message.delete(delay=10)

        manageble_events = await self.get_manageble_events(guild, author)
        if len(manageble_events) == 0:
            await ctx.send("You can't manage any events", delete_after=15)
            return

        event_str = ""
        for event in manageble_events:
            event_str += f"{event}. {manageble_events[event]['event_name']}\n"

        embed=discord.Embed(title="Select the event your like to add a attendant to", description=f"Enter the number of the list. Type `None` to cancel.\n\n{event_str}", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            return
        else:
            event_number = msg.content
            if event_number.lower() == "none":
                return

            if event_number.isnumeric() == False:
                embed=discord.Embed(title="Error", description="That is not a number", color=0xff0000)
                await dmchannel.send(embed=embed)
                return

            if int(event_number) < 1 or int(event_number) > len(manageble_events):
                embed=discord.Embed(title="Error", description="I can't find that event", color=0xff0000)
                await dmchannel.send(embed=embed)
                return
            
            selected_event = manageble_events[int(event_number)]

        embed=discord.Embed(title="Who would you like to add", description=f"Please enter the nickname or discord name of the member you would like to add.", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            return
        else:
            member_name = msg.content
            member = guild.get_member_named(member_name)

            if member is None:
                embed=discord.Embed(title="Error", description="I can't find anyone with that name", color=0xff0000)
                await dmchannel.send(embed=embed)
                return

            async with self.config.guild(guild).events() as events_list:
                event = self.event_cache[guild.id][str(selected_event["post_id"])]
                num_addending = len(event['attending'])
                if int(event["max_attendees"]) <= int(num_addending) and int(event["max_attendees"]) != 0:
                    await dmchannel.send(f"Sorry, this event is full.", delete_after=30)
                    return
                
                await dmchannel.send(f"Adding {member.mention}")
                self.event_cache[guild.id][str(selected_event["post_id"])]["attending"][member.id] = member.id
                updated_event = self.event_cache[guild.id][str(selected_event["post_id"])]
                events_list[str(selected_event["post_id"])] = updated_event
            
            message = await self.get_event_post(guild, updated_event["post_id"])
            if message is None:
                return

            embed = get_event_embed(guild=guild,event=updated_event)
            mention = get_role_mention(guild, updated_event)
            await message.edit(content=mention, embed=embed, suppress=False)

    @eventboard_manage.command("removeattending")
    @commands.guild_only()
    async def eventboard_removeattending(self, ctx: commands.Context):
        """Manually remove an attending member"""
        author = ctx.author
        guild = ctx.guild

        if author.dm_channel is None:
            dmchannel = await author.create_dm()
        else:
            dmchannel = author.dm_channel

        def same_author_check_dm(msg):
            return msg.author == author and msg.channel == dmchannel

        await ctx.message.delete(delay=10)

        manageble_events = await self.get_manageble_events(guild, author)
        if len(manageble_events) == 0:
            await ctx.send("You can't manage any events", delete_after=15)
            return

        event_str = ""
        for event in manageble_events:
            event_str += f"{event}. {manageble_events[event]['event_name']}\n"

        embed=discord.Embed(title="Select the event your like to add a attendant to", description=f"Enter the number of the list. Type `None` to cancel.\n\n{event_str}", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            return
        else:
            event_number = msg.content
            if event_number.lower() == "none":
                return

            if event_number.isnumeric() == False:
                embed=discord.Embed(title="Error", description="That is not a number", color=0xff0000)
                await dmchannel.send(embed=embed)
                return

            if int(event_number) < 1 or int(event_number) > len(manageble_events):
                embed=discord.Embed(title="Error", description="I can't find that event", color=0xff0000)
                await dmchannel.send(embed=embed)
                return
            
            selected_event = manageble_events[int(event_number)]

        embed=discord.Embed(title="Who would you like to remove", description=f"Please enter the nickname or discord name of the member you would like to add.", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
        except asyncio.TimeoutError:
            await dmchannel.send("I'm not sure where you went. We can try this again later.")
            return
        else:
            member_name = msg.content
            member = guild.get_member_named(member_name)

            if member is None:
                embed=discord.Embed(title="Error", description="I can't find anyone with that name", color=0xff0000)
                await dmchannel.send(embed=embed)
                return

            if member.id in self.event_cache[guild.id][str(selected_event["post_id"])]["attending"] is False:
                embed=discord.Embed(title="Error", description=f"{member.mention} isn't signed up for the event", color=0xff0000)
                await dmchannel.send(embed=embed)
                return

            async with self.config.guild(guild).events() as events_list:                
                await dmchannel.send(f"Removing {member.mention}")
                del self.event_cache[guild.id][str(selected_event["post_id"])]["attending"][str(member.id)]
                updated_event = self.event_cache[guild.id][str(selected_event["post_id"])]
                events_list[str(selected_event["post_id"])] = updated_event
            
            message = await self.get_event_post(guild, updated_event["post_id"])
            if message is None:
                return

            embed = get_event_embed(guild=guild,event=updated_event)
            mention = get_role_mention(guild, updated_event)
            await message.edit(content=mention, embed=embed, suppress=False)

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

        if author.dm_channel is None:
            dmchannel = await author.create_dm()
        else:
            dmchannel = author.dm_channel

        def same_author_check_dm(msg):
            return msg.author == author and msg.channel == dmchannel

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
            msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
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
            msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=600)
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
            msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
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
            msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
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

        # Mentions
        mention_all = await self.config.guild(guild).mention_all()
        if mention_all == 1:
            mentions = {}
            for role in guild.roles:
                if role.mentionable == True:
                    mentions[role.id] = role.id
        else:
            mentions = await self.config.guild(guild).mentions()

        if len(mentions) != 0:
            i = 1
            mention_str = ""
            mention_dict = {}
            for mention_id in mentions:
                role = guild.get_role(int(mention_id))
                if role is not None:
                    mention_dict[i] = mention_id
                    mention_str += f"{i}. {role.name}\n"
                    i += 1

            embed=discord.Embed(title="Who would you like to mention?", description=f"Type `None` to mention no one. Please type the corrosponding number\n\n {mention_str}", color=0xffff00)
            await dmchannel.send(embed=embed)
            try:
                msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
            except asyncio.TimeoutError:
                await dmchannel.send("I'm not sure where you went. We can try this again later.")
                return
            else:
                mention_responce = msg.content
                if mention_responce.lower() == "none":
                    mention = None
                else:
                    if not mention_responce.isnumeric():
                        embed=discord.Embed(title="Error stopping event creation", description="That doesn't seem like a correct group!", color=0xff0000)
                        await dmchannel.send(embed=embed)
                        return

                    if int(mention_responce) > len(mention_dict) and int(mention_responce) > 0:
                        embed=discord.Embed(title="Error stopping event creation", description="That doesn't seem like a correct group!", color=0xff0000)
                        await dmchannel.send(embed=embed)
                        return

                    mention = mention_dict[int(mention_responce)]

        # Image
        embed=discord.Embed(title="Would you like to add an event image?", description="Type `None` for no image. Please write an URL of an image. Must be a HTTPS url.", color=0xffff00)
        await dmchannel.send(embed=embed)
        try:
            msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
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
            "image": image,
            "remindersent": 0,
            "mention": mention
        }

        # Save event and output
        mention = get_role_mention(guild, new_event)

        post = await guild.get_channel(event_channel).send(content=mention, embed=get_event_embed(guild, new_event))
        new_event["post_id"] = post.id

        async with self.config.guild(guild).events() as event_list:
            event_list[post.id] = new_event

        await create_event_reactions(guild, post)
        if guild.id not in self.event_cache:
            self.event_cache[guild.id] = {}
        self.event_cache[guild.id][str(post.id)] = new_event
    
    @eventboard.command(name="createdebug")
    @commands.is_owner()
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
            "image": "https://media.sproutsocial.com/uploads/2017/02/10x-featured-social-media-image-size.png",
            "remindersent": 0,
            "mention": None
        }

        # Save event and output
        mention = get_role_mention(guild, new_event)

        post = await guild.get_channel(event_channel).send(content=mention, embed=get_event_embed(guild, new_event))
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

    @eventboard_settings.command(name="reminder")
    @commands.guild_only()
    async def set_guild_reminder(self, ctx: commands.Context, *, minutes: int):
        """
        Set how long before start time attending members should recieve a reminder

        `{minutes}` the number of minutes before the event starts a reminder is being send to the attending members. Set to -1 to disable reminder messages.
        """

        await self.config.guild(ctx.guild).reminder.set(int(minutes))
        await ctx.message.delete(delay=30)
        if minutes < 0:
            await ctx.channel.send("Event reminder is disabled", delete_after=30)
        else:
            await ctx.channel.send(f"A reminder will be send {minutes} minutes before the event starts", delete_after=30)

    @eventboard_settings.group(name="mentions")
    @commands.guild_only()
    async def eventboard_settings_mentions(self, ctx: commands.Context) -> None:
        """
        Configure mentionable groups
        """
        pass

    @eventboard_settings_mentions.command(name="list")
    @commands.guild_only()
    async def mention_list(self, ctx: commands.Context) -> None:
        """
        List the mentionable roles
        """
        
        roles = await self.config.guild(ctx.guild).mentions()
        role_list = ""
        i = 1

        if len(roles) == 0:
            role_list = "There are no allowed roles in the list yet!"
        else:
            role_list = ""
            for role_id in roles:
                role = ctx.guild.get_role(int(role_id))
                if role is not None:
                    role_list += f"{i}. {role.name}\n"
                    i += 1
            
        emb = discord.Embed(title="List of mentionable roles for events", description=role_list)
        await ctx.channel.send(embed=emb)

    @eventboard_settings_mentions.command(name="add")
    @commands.guild_only()
    async def mention_add(self, ctx: commands.Context, *, role_str: str) -> None:
        """
        Add a role to the allowed mentions list
        """

        await ctx.message.delete(delay=30)
        role = await get_mentionable_role(ctx.guild, role_str)
        if role is None:
            await ctx.channel.send(f"`{role_str}` can't be found", delete_after=30)
            return

        if role is False:
            await ctx.channel.send(f"`{role_str}` is not a mentionable role", delete_after=30)
            return

        async with self.config.guild(ctx.guild).mentions() as mentions_list:
            if role.id in mentions_list:
                await ctx.channel.send(f"`{role.name}` is already in the list", delete_after=30)
                return
            else:
                await ctx.channel.send(f"`{role.name}` was added to mentionable roles for events", delete_after=30)
                mentions_list[role.id] = role.id

    @eventboard_settings_mentions.command(name="delete")
    @commands.guild_only()
    async def mention_delete(self, ctx: commands.Context, *, role_str: str) -> None:
        """
        Delete a role to the allowed mentions list
        """
        
        await ctx.message.delete(delay=30)
        role = await get_mentionable_role(ctx.guild, role_str)
        if role is None:
            await ctx.channel.send(f"`{role_str}` can't be found", delete_after=30)
            return

        if role is False:
            await ctx.channel.send(f"`{role_str}` is not a mentionable role", delete_after=30)
            return

        async with self.config.guild(ctx.guild).mentions() as mentions_list:
            if str(role.id) not in mentions_list:
                await ctx.channel.send(f"`{role.name}` is not in the list", delete_after=30)
                return
            else:
                await ctx.channel.send(f"`{role.name}` was deleted to mentionable roles for events", delete_after=30)
                del mentions_list[str(role.id)]

    @eventboard_settings_mentions.command(name="all")
    @commands.guild_only()
    async def mention_all(self, ctx: commands.Context):
        """
        Toggle mentioning all mentionable groups automaticly
        """
        
        mention_all = await self.config.guild(ctx.guild).mention_all(0)
        await ctx.message.delete(delay=30)

        if mention_all == 0:
            await self.config.guild(ctx.guild).mention_all.set(1)
            await ctx.channel.send("Mention all is now **Enabled**", delete_after=30)
        else:
            await self.config.guild(ctx.guild).mention_all.set(0)
            await ctx.channel.send("Mention all is now **Disabled**", delete_after=30)

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

            def same_author_check_dm(msg):
                return msg.author == payload.member and msg.channel == dmchannel

            if payload.member.id != event["creator"] and not await self.is_mod_or_admin(payload.member):            
                await dmchannel.send("Nice try. But that event isn't yours to delete! :-1:")
                await message.remove_reaction(payload.emoji, payload.member)
                
                return

            embed=discord.Embed(title="You like to delete the selected event?", description="Please type `Y` for yes and `N` for no", color=0x0000FF)
            await dmchannel.send(embed=embed)
            try:
                msg = await self.bot.wait_for("message", check=same_author_check_dm, timeout=300)
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

                    self.event_cache[payload.guild_id][str(payload.message_id)]["attending"][str(payload.member.id)] = str(payload.member.id)
                    clean = {"declined","maybe"}

            if payload.emoji.name == "❌":
                async with self.config.guild(guild).events() as events_list:
                    self.event_cache[payload.guild_id][str(payload.message_id)]["declined"][str(payload.member.id)] = str(payload.member.id)
                    clean = {"attending","maybe"}

            if payload.emoji.name == "❔":
                async with self.config.guild(guild).events() as events_list:
                    self.event_cache[payload.guild_id][str(payload.message_id)]["maybe"][str(payload.member.id)] = str(payload.member.id)
                    clean = {"attending","declined"}

            for reactionClean in clean:
                if str(payload.user_id) in self.event_cache[payload.guild_id][str(payload.message_id)][reactionClean]:
                    del self.event_cache[payload.guild_id][str(payload.message_id)][reactionClean][str(payload.user_id)]
                    await message.remove_reaction(self.reactionEmoji[reactionClean], payload.member)

            async with self.config.guild(guild).events() as events_list:
                events_list[str(payload.message_id)] = self.event_cache[payload.guild_id][str(payload.message_id)]

            updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]
            embed = get_event_embed(guild=guild,event=updated_event)

            mention = get_role_mention(guild, updated_event)
            await message.edit(content=mention, embed=embed, suppress=False)

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
                    if str(payload.user_id) not in self.event_cache[payload.guild_id][str(payload.message_id)]["attending"]:
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]
                    else:
                        del self.event_cache[payload.guild_id][str(payload.message_id)]["attending"][str(payload.user_id)]
                        events_list[str(payload.message_id)] = self.event_cache[payload.guild_id][str(payload.message_id)]
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]
                    
                    mention = get_role_mention(guild, updated_event)
                    embed = get_event_embed(guild=guild,event=updated_event)
                    await message.edit(content=mention, embed=embed, suppress=False)
                    return

            if payload.emoji.name == "❌":
                async with self.config.guild(guild).events() as events_list:
                    if str(payload.user_id) not in self.event_cache[payload.guild_id][str(payload.message_id)]["declined"]:
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]
                    else:
                        del self.event_cache[payload.guild_id][str(payload.message_id)]["declined"][str(payload.user_id)]
                        events_list[str(payload.message_id)] = self.event_cache[payload.guild_id][str(payload.message_id)]
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]

                    mention = get_role_mention(guild, updated_event)
                    embed = get_event_embed(guild=guild,event=updated_event)
                    await message.edit(content=mention, embed=embed, suppress=False)
                    return

            if payload.emoji.name == "❔":
                async with self.config.guild(guild).events() as events_list:
                    if str(payload.user_id) not in self.event_cache[payload.guild_id][str(payload.message_id)]["maybe"]:
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]
                    else:
                        del self.event_cache[payload.guild_id][str(payload.message_id)]["maybe"][str(payload.user_id)]
                        events_list[str(payload.message_id)] = self.event_cache[payload.guild_id][str(payload.message_id)]
                        updated_event = self.event_cache[payload.guild_id][str(payload.message_id)]

                    mention = get_role_mention(guild, updated_event)
                    embed = get_event_embed(guild=guild,event=updated_event)
                    await message.edit(content=mention, embed=embed, suppress=False)
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
            log.debug("Maintenance Task Started")
            try:
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
                                mention = get_role_mention(guild, event)
                                post = await guild.get_channel(event_channel).send(content=mention, embed=get_event_embed(guild, event))
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
                            mention = get_role_mention(guild, event)
                            embed = get_event_embed(guild=guild,event=event)
                            await message.edit(content=mention, embed=embed, suppress=False)

                        reminder = int(await self.config.guild(guild).reminder())
                        if reminder >= 0:
                            if event["event_start"] < (dt.now() + timedelta(minutes=reminder)).timestamp() and int(event["remindersent"]) == 0:
                                log.debug("Sending Reminders")
                                attending = self.event_cache[guild.id][str(post_id)]['attending']
                                for memberid in attending:
                                    member = guild.get_member(int(memberid))
                                    if member is not None:
                                        if member.dm_channel is None:
                                            dmchannel = await member.create_dm()
                                        else:
                                            dmchannel = member.dm_channel

                                        temp_event = copy.copy(self.event_cache[guild.id][str(post_id)])
                                        temp_event["event_name"] = f"REMINDER: {temp_event['event_name']}"

                                        mention = get_role_mention(guild, temp_event)
                                        embed = get_event_embed(guild=guild,event=temp_event)
                                        await dmchannel.send(content=mention, embed=embed)

                                async with self.config.guild(guild).events() as event_list:
                                    self.event_cache[guild.id][str(post_id)]["remindersent"] = 1
                                    update_event = self.event_cache[guild.id][str(post_id)]
                                    event_list[str(post_id)] = update_event

                        # Clean up unknowns
                        clean = 0
                        for memberid in event["attending"]:
                            member = guild.get_member(int(memberid))
                            if member is None:
                                clean = 1
                                async with self.config.guild(guild).events() as events_list:
                                    del self.event_cache[guild.id][str(post_id)]["attending"][str(memberid)]
                                    events_list[str(post_id)] = self.event_cache[guild.id][str(post_id)]
                                    updated_event = self.event_cache[guild.id][str(post_id)]


                        for memberid in event["declined"]:
                            member = guild.get_member(int(memberid))
                            if member is None:
                                clean = 1
                                async with self.config.guild(guild).events() as events_list:
                                    del self.event_cache[guild.id][str(post_id)]["declined"][str(memberid)]
                                    events_list[str(post_id)] = self.event_cache[guild.id][str(post_id)]
                                    updated_event = self.event_cache[guild.id][str(post_id)]


                        for memberid in event["maybe"]:
                            member = guild.get_member(int(memberid))
                            if member is None:
                                clean = 1
                                async with self.config.guild(guild).events() as events_list:
                                    del self.event_cache[guild.id][str(post_id)]["maybe"][str(memberid)]
                                    events_list[str(post_id)] = self.event_cache[guild.id][str(post_id)]
                                    updated_event = self.event_cache[guild.id][str(post_id)]

                        if clean == 1:
                            embed = get_event_embed(guild=guild,event=updated_event)
                            mention = get_role_mention(guild, updated_event)
                            await message.edit(content=mention, embed=embed, suppress=False)
                        

            except Exception as e:
                log.error("Error loading events", exc_info=e)

            log.debug("Maintenance Task Stopped")
            await asyncio.sleep(CHECK_DELAY)

    async def get_manageble_events(self, guild: discord.Guild, member: discord.Member):
        event_posts = self.event_cache[guild.id]
        responce = {}
        i = 1
        for event_post in event_posts:
            event = self.event_cache[guild.id][event_post]
            if await self.is_mod_or_admin(member) == True:
                responce[i] = event
                i += 1
            elif event['creator'] == member.id:
                responce[i] = event
                i += 1
        return responce
    
    async def get_guild_event_channel(self, guild: discord.Guild) -> discord.TextChannel:
        if guild is None:
            return None
        
        event_channel_id = await self.config.guild(guild).event_channel() 
        channel = guild.get_channel(event_channel_id)

        return channel

    async def get_event_post(self, guild: discord.Guild, post_id: int, channel: discord.TextChannel=None) -> discord.Message:
        if channel is None:
            channel = await self.get_guild_event_channel(guild)
            if channel is None:
                return None
        
        post = await channel.fetch_message(post_id)
        return post