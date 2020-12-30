import contextlib
from datetime import timedelta, datetime as dt
import discord
from redbot.core import commands
from redbot.core import commands, Config

def get_event_embed(guild: discord.Guild, now: dt, event: dict) -> discord.Embed:

    eventid_str = event["id"]

    if event["description"] is None:
        emb = discord.Embed(title=event["event_name"], color=0xffff00)
    else:
        emb = discord.Embed(title=event["event_name"], description=event["description"], color=0xffff00)

    autor_str = guild.get_member(event["creator"]).nick
    if autor_str == None:
        autor_str = guild.get_member(event["creator"]).name

    if event["max_attendees"] == "0":
        attending_str = ""
    else:
        max_attendees = event["max_attendees"]
        attending_str = f" (0/{max_attendees})"
    
    starttime_str = dt.fromtimestamp(event["event_start"]).strftime("%a %d %b %Y at %H:%M")
    
    emb.add_field(name="Time", value=starttime_str, inline=False)
    emb.add_field(name=f":white_check_mark: Accepted{attending_str}", value="-", inline=True)
    emb.add_field(name=":x: Declined", value="-", inline=True)
    emb.add_field(name=":grey_question: Tentative", value="-", inline=True)
    emb.set_footer(text=f"Created by {autor_str}\nCreated *")
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
