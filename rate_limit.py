"""Per-chat Telegram pacing without modifying the shared Telegram SDK client."""
import asyncio
import time
import math
from datetime import timedelta

from telegram.error import RetryAfter


def pacing_options(config):
    """Validate panel values; an invalid setting falls back independently."""
    def number(key, default, minimum, maximum):
        value = config.get(key, default)
        if isinstance(value, bool):
            return default
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return value if math.isfinite(value) and minimum <= value <= maximum else default

    return {
        'message_gap': number('message_interval_seconds', 3.2, 0.1, 60.0),
        'request_gap': number('request_interval_seconds', 1.1, 0.1, 60.0),
        'retry_margin': number('retry_after_margin_seconds', 0.1, 0.0, 10.0),
    }


class ClientCooldown:
    def __init__(self):
        self.until = 0.0


class ChatPacer:
    def __init__(self, request_gap=1.1, message_gap=3.2, clock=time.monotonic, sleep=asyncio.sleep, cooldown=None, retry_margin=0.1):
        self.stream_lock = asyncio.Lock()
        self.request_lock = asyncio.Lock()
        self.request_gap = request_gap
        self.message_gap = message_gap
        self.clock = clock
        self.sleep = sleep
        self.next_request = 0.0
        self.next_message = 0.0
        self.cooldown = cooldown if cooldown is not None else ClientCooldown()
        self.retry_margin = retry_margin
        self.last_request = None
        self.last_message = None

    def configure(self, request_gap, message_gap, retry_margin):
        self.request_gap = request_gap
        self.message_gap = message_gap
        self.retry_margin = retry_margin
        # Preserve outstanding deadlines, including a server RetryAfter.
        if self.last_request is not None:
            self.next_request = max(self.next_request, self.last_request + request_gap)
        if self.last_message is not None:
            self.next_message = max(self.next_message, self.last_message + message_gap)

    async def call(self, method, is_message, *args, **kwargs):
        async with self.request_lock:
            while True:
                while True:
                    deadline = max(self.next_request, self.next_message if is_message else 0.0, self.cooldown.until)
                    delay = deadline - self.clock()
                    if delay <= 0:
                        break
                    await self.sleep(delay)
                now = self.clock()
                self.last_request = now
                self.next_request = now + self.request_gap
                if is_message:
                    self.last_message = now
                    self.next_message = now + self.message_gap
                try:
                    return await method(*args, **kwargs)
                except RetryAfter as exc:
                    wait = exc.retry_after
                    seconds = wait.total_seconds() if isinstance(wait, timedelta) else float(wait)
                    # Never shorten Telegram's requested backoff. Keep it shared
                    # with all queued calls, including edits and later streams.
                    self.next_request = max(self.next_request, self.clock() + max(seconds, 0) + self.retry_margin)
                    self.cooldown.until = max(self.cooldown.until, self.next_request)
                    # Retry only a rejected request. Cancellation and all other
                    # failures propagate, avoiding duplicate timeout retries.


class PacedClient:
    def __init__(self, client, pacer, chat_id):
        self.original_client = client
        self.pacer = pacer
        self.chat_id = str(chat_id)

    def __getattr__(self, name):
        return getattr(self.original_client, name)

    async def send_message(self, *args, **kwargs):
        chat_id = kwargs.get('chat_id', args[0] if args else None)
        method = self.original_client.send_message
        if str(chat_id) != self.chat_id:
            return await method(*args, **kwargs)
        return await self.pacer.call(method, True, *args, **kwargs)

    async def edit_message_text(self, *args, **kwargs):
        chat_id = kwargs.get('chat_id', args[1] if len(args) > 1 else None)
        method = self.original_client.edit_message_text
        if str(chat_id) != self.chat_id:
            return await method(*args, **kwargs)
        return await self.pacer.call(method, False, *args, **kwargs)
