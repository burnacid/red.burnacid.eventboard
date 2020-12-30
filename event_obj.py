import re
from typing import List, Optional, Tuple, cast

import discord
from discord.ext.commands.converter import Converter
from discord.ext.commands.errors import BadArgument

IMAGE_LINKS = re.compile(r"(http[s]?:\/\/[^\"\']*\.(?:png|jpg|jpeg|gif|png))", flags=re.I)

class Event:
    creator: discord.Member
    create_time: datetime
    event_name: str
    description: Optional[str]
    max_attendees: int
    event_start: timestamp
    post_id: int
    attending: Optional[List[discord.Member]]
    declined: Optional[List[discord.Member]]
    maybe: Optional[List[discord.Member]]

    def __init__(self, **kwargs):
        self.creator = kwargs.get("creator")
        self.create_time = kwargs.get("create_time")
        self.event_name = kwargs.get("event_name")
        self.description = kwargs.get("description")
        self.max_attendees = kwargs.get("max_attendees")
        self.event_start = kwargs.get("event_start")
        self.post_id = kwargs.get("post_id")
        self.attending = kwargs.get("attending", [])
        self.declined = kwargs.get("declined", [])
        self.maybe = kwargs.get("maybe", [])

    def to_json(self):
        return {
            "creator": self.hoster.id,
            "create_time": [(m.id, p_class) for m, p_class in self.members],
            "event_name": self.event,
            "description": self.max_slots,
            "max_attendees": self.approver.id if self.approver else None,
            "event_start": self.message.id if self.message else None,
            "post_id": self.channel.id if self.channel else None,
            "attending": [m.id for m in self.maybe],
            "declined": ,
            "maybe": ,
        }