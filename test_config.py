import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_integration import module, MessageChain, Plain, MessageType, source
from test_rate_limit import Clock, FakeClient
from astrbot_plugin_telegram_stream_segments.rate_limit import ChatPacer, pacing_options
from telegram.error import RetryAfter


class ConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_defaults_match_schema_and_accept_numeric_strings(self):
        schema = json.loads(Path(__file__).with_name('_conf_schema.json').read_text(encoding='utf-8'))
        defaults = {key: value['default'] for key, value in schema.items()}
        self.assertEqual(pacing_options(defaults), pacing_options({}))
        self.assertEqual(pacing_options({'message_interval_seconds': '0.1', 'request_interval_seconds': 60, 'retry_after_margin_seconds': 0}), {'message_gap': 0.1, 'request_gap': 60.0, 'retry_margin': 0.0})

    def test_invalid_values_fall_back_independently(self):
        for key, output, default in [('message_interval_seconds', 'message_gap', 3.2), ('request_interval_seconds', 'request_gap', 1.1), ('retry_after_margin_seconds', 'retry_margin', 0.1)]:
            for value in [None, True, False, '', 'bad', [], {}, float('nan'), float('inf'), '-Infinity', -1, 61]:
                with self.subTest(key=key, value=value):
                    result = pacing_options({key: value})
                    self.assertEqual(result[output], default)
            if key != 'retry_after_margin_seconds':
                self.assertEqual(pacing_options({key: 0})[output], default)
        self.assertEqual(pacing_options({'retry_after_margin_seconds': 11})['retry_margin'], 0.1)

    async def test_custom_intervals_and_margin_affect_actual_waits(self):
        clock = Clock()
        client = FakeClient(clock)
        pacer = ChatPacer(clock=clock.time, sleep=clock.sleep)
        pacer.configure(**pacing_options({'message_interval_seconds': 8, 'request_interval_seconds': 2, 'retry_after_margin_seconds': 0.7}))
        await pacer.call(client.send_message, True, chat_id='-100', text='first')
        await pacer.call(client.send_message, True, chat_id='-100', text='second')
        self.assertAlmostEqual(client.attempts[1][1] - client.attempts[0][1], 8)
        client.edit_errors = [RetryAfter(17)]
        await pacer.call(client.edit_message_text, False, chat_id='-100', message_id=2, text='updated')
        self.assertAlmostEqual(client.attempts[-1][1] - client.attempts[-2][1], 17.7)
        self.assertAlmostEqual(client.attempts[-2][1] - client.attempts[1][1], 2)

    async def test_zero_margin_still_waits_full_server_deadline(self):
        clock = Clock()
        client = FakeClient(clock, [RetryAfter(17)])
        pacer = ChatPacer(clock=clock.time, sleep=clock.sleep)
        pacer.configure(**pacing_options({'message_interval_seconds': 0.1, 'request_interval_seconds': 0.1, 'retry_after_margin_seconds': 0}))
        await pacer.call(client.send_message, True, chat_id='-100', text='first')
        self.assertGreaterEqual(client.attempts[1][1] - client.attempts[0][1], 17)

    async def test_wrapper_reads_once_per_stream_and_reuses_pacer(self):
        cls = module.TelegramPlatformEvent
        previous = cls.send_streaming
        config = {'enable': True, 'message_interval_seconds': 5}
        snapshots = []

        async def sender(event, generator, *args, **kwargs):
            snapshots.append(event.client.pacer.message_gap)
            config['message_interval_seconds'] = 9
            snapshots.append(event.client.pacer.message_gap)
            return [c async for c in generator]

        cls.send_streaming = sender
        plugin = module.TelegramStreamSegments(SimpleNamespace(), config)
        event = SimpleNamespace(get_message_type=lambda: MessageType.GROUP_MESSAGE, client=object(), message_obj=SimpleNamespace(group_id='-100'))
        try:
            await plugin.initialize()
            await cls.send_streaming(event, source([MessageChain([Plain('a')])]))
            first_pacer = next(iter(plugin._pacers.values()))
            await cls.send_streaming(event, source([MessageChain([Plain('b')])]))
            self.assertEqual(snapshots, [5, 5, 9, 9])
            self.assertIs(next(iter(plugin._pacers.values())), first_pacer)
        finally:
            await plugin.terminate()
            cls.send_streaming = previous

    def test_reconfigure_keeps_reserved_server_deadline(self):
        pacer = ChatPacer()
        pacer.next_request = 500
        pacer.cooldown.until = 700
        pacer.last_request = 100
        pacer.configure(**pacing_options({'request_interval_seconds': 0.1}))
        self.assertEqual(pacer.next_request, 500)
        self.assertEqual(pacer.cooldown.until, 700)


if __name__ == '__main__':
    unittest.main()
