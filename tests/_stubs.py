
# -*- coding: utf-8 -*-
"""Minimal AstrBot API stubs so the plugin can be imported outside AstrBot."""

from __future__ import annotations

import logging
import sys
import types
from types import SimpleNamespace


class _EventMessageType:
    ALL = "ALL"
    GROUP_MESSAGE = "GROUP_MESSAGE"
    PRIVATE_MESSAGE = "PRIVATE_MESSAGE"


class _Filter:
    EventMessageType = _EventMessageType

    def event_message_type(self, *_args, **_kwargs):
        return lambda func: func

    def regex(self, *_args, **_kwargs):
        return lambda func: func

    def command(self, *_args, **_kwargs):
        return lambda func: func


class _Image:
    @classmethod
    def fromFileSystem(cls, path):
        return ("image_file", str(path))

    @classmethod
    def fromURL(cls, url):
        return ("image_url", str(url))

    @classmethod
    def fromBytes(cls, data):
        return ("image_bytes", data)


class _Plain:
    def __init__(self, text):
        self.text = text


class _Star:
    def __init__(self, context=None):
        self.context = context

    async def html_render(self, *_args, **_kwargs):
        raise RuntimeError("html_render is unavailable in the stub")


class FakeEvent:
    """Records what the handler yields and mimics the event surface."""

    def __init__(self, text: str, admin: bool = False, sender: str = "tester"):
        self.message_obj = SimpleNamespace(message_str=text)
        self._admin = admin
        self._sender = sender
        self.results: list = []
        self.stopped = False

    def get_message_str(self) -> str:
        return self.message_obj.message_str

    def stop_event(self) -> None:
        self.stopped = True

    def plain_result(self, text):
        return ("plain", text)

    def image_result(self, path):
        return ("image", str(path))

    def chain_result(self, chain):
        return ("chain", chain)

    def get_sender_id(self):
        return self._sender

    def get_self_id(self):
        return "1"

    def is_admin(self) -> bool:
        return self._admin


def install() -> None:
    if "astrbot.api" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("astrbot")
    api.AstrBotConfig = dict
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = FakeEvent
    event.filter = _Filter()
    star = types.ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = _Star
    star.register = lambda *_args, **_kwargs: (lambda cls: cls)
    components = types.ModuleType("astrbot.api.message_components")
    components.Image = _Image
    components.Plain = _Plain
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event
    sys.modules["astrbot.api.star"] = star
    sys.modules["astrbot.api.message_components"] = components
