"""Run from this folder with the installed AstrBot venv; never sends messages."""
import asyncio
import importlib
import os
import sys
import unittest
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

if core_root := os.environ.get('ASTRBOT_ROOT'):
    sys.path.insert(0, str(Path(core_root).expanduser().resolve()))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    importlib.import_module('astrbot')
except ModuleNotFoundError as exc:
    if exc.name != 'astrbot':
        raise
    raise unittest.SkipTest('AstrBot is unavailable: use its Python environment and optionally set ASTRBOT_ROOT to the AstrBot source root.') from exc
module = importlib.import_module('astrbot_plugin_telegram_stream_segments.main')
Plain, MessageChain = module.Plain, module.MessageChain
MessageType = module.MessageType


async def source(chains):
    for chain in chains:
        yield chain


async def collect(chains):
    return [chain async for chain in module.segment_stream(source(chains))]


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_types_and_metadata(self):
        chain = MessageChain([Plain('a\n\nb')], use_markdown_=True)
        out = await collect([chain])
        self.assertEqual([c.type for c in out], [None, 'break', None])
        self.assertEqual([c.chain[0].text for c in out if c.chain], ['a', 'b'])
        self.assertTrue(out[0].use_markdown_)

    async def test_mixed_chain_keeps_media_and_fence_state(self):
        from astrbot.api.message_components import Image
        image = Image.fromURL('https://example.invalid/image.png')
        mixed = MessageChain([image, Plain('```\nx\n\ny')])
        out = await collect([mixed, MessageChain([Plain('\n```\n\nnext')])])
        self.assertIs(out[0].chain[0], image)
        self.assertEqual(out[0].chain[1].text, '```\nx\n\ny')
        self.assertEqual(sum(c.type == 'break' for c in out), 1)

    async def test_original_break_and_controls(self):
        original = MessageChain([], type='break')
        out = await collect([MessageChain([Plain('a')]), original, MessageChain([Plain('b')])])
        self.assertIs(out[1], original)
        self.assertEqual(len(out), 3)

    async def test_no_empty_outputs(self):
        out = await collect([MessageChain([]), MessageChain([Plain('')]), MessageChain([Plain('\n\n')])])
        self.assertEqual(out, [])

    async def test_lifecycle_private_disabled_and_args(self):
        cls = module.TelegramPlatformEvent
        real = cls.send_streaming
        calls = []

        async def fake(event, generator, *args, **kwargs):
            calls.append((generator, args, kwargs))
            return [c async for c in generator]

        cls.send_streaming = fake
        plugin = module.TelegramStreamSegments(SimpleNamespace(), {'enable': True})
        duplicate = module.TelegramStreamSegments(SimpleNamespace(), {'enable': True})
        try:
            await plugin.initialize()
            wrapped = cls.send_streaming
            await plugin.initialize()
            await duplicate.initialize()
            self.assertIs(cls.send_streaming, wrapped)
            group = SimpleNamespace(get_message_type=lambda: MessageType.GROUP_MESSAGE, client=object(), message_obj=SimpleNamespace(group_id='-100'))
            private = SimpleNamespace(get_message_type=lambda: MessageType.FRIEND_MESSAGE)
            chains = [MessageChain([Plain('a\n\nb')])]
            result = await cls.send_streaming(group, source(chains), use_fallback=True)
            self.assertEqual(sum(c.type == 'break' for c in result), 1)
            self.assertEqual(calls[-1][2], {'use_fallback': True})
            gen = source(chains)
            self.assertEqual(await cls.send_streaming(private, gen, False), chains)
            self.assertIs(calls[-1][0], gen)
            plugin.config['enable'] = False
            gen = source(chains)
            self.assertEqual(await cls.send_streaming(group, gen), chains)
            self.assertIs(calls[-1][0], gen)
            await duplicate.terminate()
            self.assertIs(cls.send_streaming, wrapped)
            await plugin.terminate()
            self.assertIs(cls.send_streaming, fake)
        finally:
            cls.send_streaming = real

    async def test_later_wrapper_is_not_overwritten_on_unload(self):
        cls = module.TelegramPlatformEvent
        real = cls.send_streaming

        async def fake(event, generator, *args, **kwargs):
            return [c async for c in generator]

        cls.send_streaming = fake
        plugin = module.TelegramStreamSegments(SimpleNamespace(), {'enable': True})
        try:
            await plugin.initialize()
            ours = cls.send_streaming
            @wraps(ours)
            async def later(event, generator, *args, **kwargs):
                return await ours(event, generator, *args, **kwargs)
            cls.send_streaming = later
            await plugin.terminate()
            self.assertIs(cls.send_streaming, later)
            chains = [MessageChain([Plain('a\n\nb')])]
            group = SimpleNamespace(get_message_type=lambda: MessageType.GROUP_MESSAGE, client=object(), message_obj=SimpleNamespace(group_id='-100'))
            self.assertEqual(await later(group, source(chains)), chains)
            reloaded = module.TelegramStreamSegments(SimpleNamespace(), {'enable': True})
            await reloaded.initialize()
            self.assertIsNot(cls.send_streaming, later)
            self.assertEqual(sum(c.type == 'break' for c in await cls.send_streaming(group, source(chains))), 1)
            await reloaded.terminate()
            self.assertIs(cls.send_streaming, later)
        finally:
            cls.send_streaming = real


class ProxyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_core_builder_both_requests_and_handlers_without_network(self):
        from unittest.mock import patch
        import httpx
        from telegram.ext import ApplicationBuilder
        from telegram.request import HTTPXRequest
        from astrbot.core.platform.sources.telegram import tg_adapter
        from astrbot_plugin_telegram_stream_segments.telegram_proxy import TelegramProxyHook
        created = []
        class RecordingRequest(HTTPXRequest):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.options = kwargs
                created.append(self)
        adapter = object.__new__(tg_adapter.TelegramPlatformAdapter)
        adapter.config = {'telegram_token': '123456:TEST_TOKEN_FOR_OFFLINE_TEST_ONLY'}
        adapter.base_url = 'https://api.example.invalid/bot'
        adapter.file_base_url = 'https://api.example.invalid/file/bot'
        original = tg_adapter.ApplicationBuilder
        hook = TelegramProxyHook(tg_adapter, {
            'telegram_proxy_enable': True, 'telegram_proxy_url': 'http://127.0.0.1:7897'
        }, RecordingRequest)
        try:
            hook.install()
            # Even an unusable global proxy must not affect these requests.
            with patch.dict(os.environ, {'HTTP_PROXY': 'broken://global', 'HTTPS_PROXY': 'broken://global'}), \
                    patch.object(httpx.AsyncClient, 'send', side_effect=AssertionError('network forbidden')):
                adapter._build_application()
            self.assertIs(adapter.client, adapter.application.bot)
            self.assertTrue(adapter.client.base_url.startswith(adapter.base_url))
            self.assertTrue(adapter.client.base_file_url.startswith(adapter.file_base_url))
            self.assertEqual(len(adapter.application.handlers[0]), 1)
            self.assertEqual(adapter.application.handlers[0][0].callback, adapter.message_handler)
            self.assertIs(adapter.client.request, created[0])
            self.assertIs(adapter.client._request[0], created[1])
            self.assertEqual([r.options['connection_pool_size'] for r in created], [256, 1])
            self.assertTrue(all(r.options['httpx_kwargs'] == {'trust_env': False} for r in created))
            self.assertFalse(created[0]._client._trust_env)
            self.assertFalse(created[1]._client._trust_env)
            self.assertIs(__import__('telegram.ext', fromlist=['ApplicationBuilder']).ApplicationBuilder, ApplicationBuilder)
            hook.uninstall()
            self.assertIs(tg_adapter.ApplicationBuilder, original)
            self.assertIs(adapter.client.request, created[0])  # Existing client unchanged.
        finally:
            hook.uninstall()
            for request in created:
                await request.shutdown()

    async def test_plugin_proxy_lifecycle_with_segmentation_disabled(self):
        from astrbot.core.platform.sources.telegram import tg_adapter
        original = tg_adapter.ApplicationBuilder
        plugin = module.TelegramStreamSegments(SimpleNamespace(), {
            'enable': False, 'telegram_proxy_enable': True, 'telegram_proxy_url': ''
        })
        try:
            await plugin.initialize()
            self.assertIsNot(tg_adapter.ApplicationBuilder, original)
            with self.assertRaises(ValueError):
                tg_adapter.ApplicationBuilder()
        finally:
            await plugin.terminate()
        self.assertIs(tg_adapter.ApplicationBuilder, original)


if __name__ == '__main__':
    unittest.main()
