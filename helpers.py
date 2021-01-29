import contextlib
from datetime import timedelta, datetime as dt
import discord
from redbot.core import commands
from redbot.core import commands, Config
from discord.ext.commands.errors import BadArgument

import re

import logging

IMAGE_LINKS = re.compile(r"(http[s]?:\/\/[^\"\']*\.(?:png|jpg|jpeg|gif|png))", flags=re.I)
log = logging.getLogger("red.burnacid.eventboard")

def get_role_mention(guild: discord.Guild, event: dict):
    if event["mention"] is None:
        return None

    role = guild.get_role(int(event["mention"]))
    if role is None:
        return None
    
    return role.mention

def get_event_embed(guild: discord.Guild, event: dict) -> discord.Embed:

    if event["description"] is None:
        emb = discord.Embed(title=event["event_name"], color=0xffff00)
    else:
        emb = discord.Embed(title=event["event_name"], description=event["description"], color=0xffff00)

    autor_str = guild.get_member(event["creator"]).nick
    if autor_str == None:
        autor_str = guild.get_member(event["creator"]).name
    attending = len(event["attending"])

    if event["max_attendees"] == "0":
        attending_str = ""
    else:
        max_attendees = event["max_attendees"]
        attending_str = f" ({attending}/{max_attendees})"

    if attending == 0:
        attending_members = "-"
    else:
        attending_members = ""
        for memberid in event["attending"]:
            member = guild.get_member(int(memberid))
            if member is None:
                member_str = "Unknown"
            else:
                member_str = guild.get_member(int(memberid)).mention
            attending_members += f"{member_str}\n"

    if len(event["declined"]) == 0:
        declined_members = "-"
    else:
        declined_members = ""
        for memberid in event["declined"]:
            member = guild.get_member(int(memberid))
            if member is None:
                member_str = "Unknown"
            else:
                member_str = guild.get_member(int(memberid)).mention
            declined_members += f"{member_str}\n"

    if len(event["maybe"]) == 0:
        maybe_members = "-"
    else:
        maybe_members = ""
        for memberid in event["maybe"]:
            member = guild.get_member(int(memberid))
            if member is None:
                member_str = "Unknown"
            else:
                member_str = guild.get_member(int(memberid)).mention
            maybe_members += f"{member_str}\n"

    if event["image"] is not None:
        emb.set_image(url=event["image"])
    
    starttime_str = dt.fromtimestamp(event["event_start"]).strftime("%a %d %b %Y at %H:%M")
    createtime_str = dt.fromtimestamp(event["create_time"]).strftime("%a %d %b %Y at %H:%M")

    emb.add_field(name="Time", value=starttime_str, inline=False)
    emb.add_field(name=f":white_check_mark: Accepted{attending_str}", value=attending_members, inline=True)
    emb.add_field(name=":x: Declined", value=declined_members, inline=True)
    emb.add_field(name=":grey_question: Tentative", value=maybe_members, inline=True)
    emb.set_footer(text=f"Created by {autor_str}\nCreated on {createtime_str}")
    return emb

async def create_event_reactions(guild: discord.guild, post):
    check = "✅"
    cross = "❌"
    maybe = "❔"
    trash = "🗑️"
    await post.add_reaction(check)
    await post.add_reaction(cross)
    await post.add_reaction(maybe)
    await post.add_reaction(trash)
    return
    
async def valid_image(argument):
    search = IMAGE_LINKS.search(argument)
    if not search:
        return False
    else:
        return True

async def get_mentionable_role(guild: discord.Guild, role_name: str) -> discord.Role:
    for role in guild.roles:
        if role.name.lower() == role_name.lower():
            if role.mentionable == True:
                return role
            else:
                return False
    return None