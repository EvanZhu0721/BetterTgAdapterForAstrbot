"""Opt-in Telegram group streaming paragraph boundaries."""
from functools import wraps

from astrbot.api import AstrBotConfig, logger
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import MessageType
from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent

from .splitter import ParagraphSplitter
from .rate_limit import ChatPacer, ClientCooldown, PacedClient, pacing_options

_PATCH_MARKER = "_astrbot_telegram_stream_segments_patch"


async def segment_stream(generator):
    splitter = ParagraphSplitter()
    last_chain = MessageChain()
    async for chain in generator:
        if not isinstance(chain, MessageChain):
            yield chain
            continue
        if chain.type is not None:
            for text in splitter.flush():
                yield last_chain.derive([Plain(text)])
            yield chain
            splitter = ParagraphSplitter()
            continue
        if any(not isinstance(c, Plain) for c in chain.chain):
            # Keep original mixed chains together: the Telegram adapter cannot
            # safely send a newly introduced media-only chain with empty delta.
            components = list(chain.chain)
            first_plain = next((i for i, c in enumerate(components) if isinstance(c, Plain)), None)
            if first_plain is not None:
                prefix = splitter.pending
                splitter.pending = ""
                if prefix:
                    components[first_plain] = Plain(prefix + components[first_plain].text)
                for component in chain.chain:
                    if isinstance(component, Plain):
                        splitter.feed(component.text)  # Track fences without splitting this chain.
                splitter.pending = ""  # This chain delivers its own trailing whitespace.
            yield chain.derive(components)
            last_chain = chain
            continue
        last_chain = chain
        for component in chain.chain:
            for text in splitter.feed(component.text):
                if text is None:
                    yield MessageChain(chain=[], type="break")
                else:
                    yield chain.derive([Plain(text)])
    for text in splitter.flush():
        yield last_chain.derive([Plain(text)])


@register("astrbot_plugin_telegram_stream_segments", "Local", "Telegram 群聊流式空行分段", "1.0.2")
class TelegramStreamSegments(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._wrapper = None
        self._previous = None
        self._active = False
        self._pacers = {}
        self._client_states = {}

    async def initialize(self):
        current = TelegramPlatformEvent.send_streaming
        if self._wrapper is current:
            return
        existing_owner = getattr(current, _PATCH_MARKER, None)
        if existing_owner is not None and getattr(existing_owner, "_active", False):
            logger.warning("[TelegramStreamSegments] Another instance is active; duplicate patch skipped.")
            return
        self._previous = current
        self._active = True
        owner = self

        @wraps(current)
        async def wrapped(event, generator, *args, **kwargs):
            if owner._active and owner.config.get("enable", True) and event.get_message_type() == MessageType.GROUP_MESSAGE:
                client = event.client
                while isinstance(client, PacedClient):
                    client = client.original_client
                chat_id = str(event.message_obj.group_id).split("#", 1)[0]
                key = (id(client), chat_id)
                _, cooldown = owner._client_states.setdefault(id(client), (client, ClientCooldown()))
                pacer = owner._pacers.setdefault(key, ChatPacer(cooldown=cooldown))
                async with pacer.stream_lock:
                    pacer.configure(**pacing_options(owner.config))
                    previous_client = event.client
                    proxy = PacedClient(client, pacer, chat_id)
                    event.client = proxy
                    try:
                        return await current(event, segment_stream(generator), *args, **kwargs)
                    finally:
                        if event.client is proxy:
                            event.client = previous_client
            return await current(event, generator, *args, **kwargs)

        setattr(wrapped, _PATCH_MARKER, self)
        self._wrapper = wrapped
        TelegramPlatformEvent.send_streaming = wrapped
        logger.info("[TelegramStreamSegments] Telegram group streaming blank-line segmentation enabled.")

    async def terminate(self):
        self._active = False
        if self._wrapper is not None and TelegramPlatformEvent.send_streaming is self._wrapper:
            TelegramPlatformEvent.send_streaming = self._previous
        self._wrapper = None
        self._previous = None
