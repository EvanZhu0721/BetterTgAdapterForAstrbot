import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_integration import module, MessageChain, Plain, MessageType, source
from astrbot_plugin_telegram_stream_segments.rate_limit import ChatPacer, ClientCooldown, PacedClient
from telegram.error import RetryAfter, TimedOut


class Clock:
    def __init__(self):
        self.now = 100.0
        self.waits = []

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


class FakeClient:
    def __init__(self, clock, send_errors=(), edit_errors=()):
        self.clock = clock
        self.send_errors = list(send_errors)
        self.edit_errors = list(edit_errors)
        self.attempts = []
        self.messages = {}

    async def send_chat_action(self, **kwargs):
        return True

    async def send_message(self, **kwargs):
        self.attempts.append(('send', self.clock.time(), kwargs.copy()))
        if self.send_errors:
            raise self.send_errors.pop(0)
        assert kwargs['text'].strip(), 'Empty Telegram message'
        message_id = len(self.messages) + 1
        self.messages[message_id] = kwargs['text']
        return SimpleNamespace(message_id=message_id)

    async def edit_message_text(self, **kwargs):
        self.attempts.append(('edit', self.clock.time(), kwargs.copy()))
        if self.edit_errors:
            raise self.edit_errors.pop(0)
        self.messages[kwargs['message_id']] = kwargs['text']
        return SimpleNamespace(message_id=kwargs['message_id'])


def event_for(client, chat='-100'):
    event = module.TelegramPlatformEvent.__new__(module.TelegramPlatformEvent)
    event.client = client
    event.message_obj = SimpleNamespace(group_id=chat, type=MessageType.GROUP_MESSAGE)
    event.platform_meta = SimpleNamespace(name='telegram')
    return event


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_timedelta_property_and_repeated_429(self):
        with patch.dict(os.environ, {'PTB_TIMEDELTA': 'true'}):
            clock = Clock()
            error = RetryAfter(timedelta(seconds=2))
            self.assertIsInstance(error.retry_after, timedelta)
            client = FakeClient(clock, [error, RetryAfter(timedelta(seconds=5))])
            pacer = ChatPacer(clock=clock.time, sleep=clock.sleep)
            await pacer.call(client.send_message, True, chat_id='-100', text='a')
            self.assertEqual(len(client.attempts), 3)
            self.assertGreaterEqual(client.attempts[2][1] - client.attempts[1][1], 5)
            self.assertEqual(list(client.messages.values()), ['a'])

    async def test_real_core_send_and_edit_429_retries_preserve_paragraphs(self):
        clock = Clock()
        client = FakeClient(clock, [RetryAfter(17)], [RetryAfter(timedelta(seconds=4))])
        event = event_for(client)
        pacer = ChatPacer(clock=clock.time, sleep=clock.sleep)
        event.client = PacedClient(client, pacer, '-100')
        # Exercise installed _send_streaming_edit, not a replacement sender.
        stream = module.segment_stream(source([MessageChain([Plain('First\n\nSecond\n\nThird')])]))
        await event._send_streaming_edit('-100', None, {'chat_id': '-100'}, stream)
        self.assertEqual(list(client.messages.values()), ['First', 'Second', 'Third'])
        sends = [item for item in client.attempts if item[0] == 'send']
        self.assertEqual(len(sends), 4)
        self.assertGreaterEqual(sends[1][1] - sends[0][1], 17)
        self.assertTrue(any(wait >= 17 for wait in clock.waits))
        for first, second in zip(sends[1:], sends[2:]):
            self.assertGreaterEqual(second[1] - first[1], 3.2 - 1e-8)

    async def test_shared_bot_cooldown_respected_by_other_chat(self):
        clock = Clock()
        cooldown = ClientCooldown()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold_sleep(seconds):
            entered.set()
            await release.wait()
            clock.now += seconds

        first = ChatPacer(clock=clock.time, sleep=hold_sleep, cooldown=cooldown)
        second = ChatPacer(clock=clock.time, sleep=clock.sleep, cooldown=cooldown)
        client = FakeClient(clock, [RetryAfter(17)])
        task = asyncio.create_task(first.call(client.send_message, True, chat_id='-100', text='a'))
        await entered.wait()
        await second.call(client.send_message, True, chat_id='-200', text='b')
        self.assertGreaterEqual(client.attempts[1][1], 117)
        release.set()
        await task

    async def test_network_timeout_is_not_retried(self):
        clock = Clock()
        client = FakeClient(clock, [TimedOut()])
        pacer = ChatPacer(clock=clock.time, sleep=clock.sleep)
        with self.assertRaises(TimedOut):
            await pacer.call(client.send_message, True, chat_id='-100', text='a')
        self.assertEqual(len(client.attempts), 1)

    async def test_full_wrapper_cancel_restores_client_and_releases_locks(self):
        clock = Clock()
        client = FakeClient(clock, [RetryAfter(17)])
        event = event_for(client)
        plugin = module.TelegramStreamSegments(SimpleNamespace(), {'enable': True})
        waiting = asyncio.Event()

        async def cancellable_sleep(seconds):
            waiting.set()
            await asyncio.Event().wait()

        pacer = ChatPacer(clock=clock.time, sleep=cancellable_sleep)
        plugin._pacers[(id(client), '-100')] = pacer
        await plugin.initialize()
        try:
            task = asyncio.create_task(event.send_streaming(source([MessageChain([Plain('a')])])))
            await waiting.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertIs(event.client, client)
            self.assertFalse(pacer.stream_lock.locked())
            self.assertFalse(pacer.request_lock.locked())
        finally:
            await plugin.terminate()

    async def test_same_event_concurrent_streams_serialize_and_restore(self):
        clock = Clock()
        client = FakeClient(clock)
        event = event_for(client, '-100#7')
        plugin = module.TelegramStreamSegments(SimpleNamespace(), {'enable': True})
        pacer = ChatPacer(clock=clock.time, sleep=clock.sleep)
        plugin._pacers[(id(client), '-100')] = pacer
        entered = asyncio.Event()
        release = asyncio.Event()
        second_consumed = asyncio.Event()

        async def first_stream():
            yield MessageChain([Plain('a')])
            entered.set()
            await release.wait()
            yield MessageChain([Plain('\n\nb')])

        async def second_stream():
            second_consumed.set()
            yield MessageChain([Plain('c')])

        async def no_metric(**kwargs):
            pass

        await plugin.initialize()
        try:
            with patch('astrbot.core.platform.sources.telegram.tg_event.Metric.upload', no_metric):
                first = asyncio.create_task(event.send_streaming(first_stream()))
                await entered.wait()
                second = asyncio.create_task(event.send_streaming(second_stream()))
                await asyncio.sleep(0)
                self.assertFalse(second_consumed.is_set())
                release.set()
                await asyncio.gather(first, second)
                await asyncio.sleep(0)
            self.assertIs(event.client, client)
            self.assertEqual(list(client.messages.values()), ['a', 'b', 'c'])
            self.assertEqual(len(plugin._pacers), 1)
        finally:
            await plugin.terminate()

    async def test_same_chat_two_events_share_stream_lock(self):
        clock = Clock()
        client = FakeClient(clock)
        first_event = event_for(client, '-100#7')
        second_event = event_for(client, '-100#9')
        plugin = module.TelegramStreamSegments(SimpleNamespace(), {'enable': True})
        plugin._pacers[(id(client), '-100')] = ChatPacer(clock=clock.time, sleep=clock.sleep)
        entered, release, consumed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def first_stream():
            yield MessageChain([Plain('first')])
            entered.set()
            await release.wait()
            yield MessageChain([Plain('\n\nlast')])

        async def second_stream():
            consumed.set()
            yield MessageChain([Plain('second')])

        async def no_metric(**kwargs):
            pass

        await plugin.initialize()
        try:
            with patch('astrbot.core.platform.sources.telegram.tg_event.Metric.upload', no_metric):
                first = asyncio.create_task(first_event.send_streaming(first_stream()))
                await entered.wait()
                second = asyncio.create_task(second_event.send_streaming(second_stream()))
                await asyncio.sleep(0)
                self.assertFalse(consumed.is_set())
                release.set()
                await asyncio.gather(first, second)
                await asyncio.sleep(0)
            self.assertEqual(list(client.messages.values()), ['first', 'last', 'second'])
            self.assertIs(first_event.client, client)
            self.assertIs(second_event.client, client)
        finally:
            await plugin.terminate()


if __name__ == '__main__':
    unittest.main()
