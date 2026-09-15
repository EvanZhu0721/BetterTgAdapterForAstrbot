"""Network-free builder injection, validation and lifecycle checks."""
from functools import wraps
from types import SimpleNamespace
import unittest

from telegram_proxy import TelegramProxyHook, validate_proxy_url


class Builder:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.requests = {}

    def request(self, request):
        self.requests['api'] = request
        return self

    def get_updates_request(self, request):
        self.requests['updates'] = request
        return self


class ProxyTests(unittest.TestCase):
    def test_disabled_passthrough(self):
        module = SimpleNamespace(ApplicationBuilder=Builder)
        def fail(**kwargs):
            self.fail('disabled proxy constructed a request')
        hook = TelegramProxyHook(module, {}, fail)
        hook.install()
        result = module.ApplicationBuilder(3, option=True)
        self.assertEqual(result.args, (3,))
        self.assertEqual(result.kwargs, {'option': True})
        self.assertEqual(result.requests, {})
        hook.uninstall()
        self.assertIs(module.ApplicationBuilder, Builder)

    def test_both_requests_isolation_and_independent_switch(self):
        module = SimpleNamespace(ApplicationBuilder=Builder)
        config = {'enable': False, 'telegram_proxy_enable': True,
                  'telegram_proxy_url': 'http://127.0.0.1:7897'}
        hook = TelegramProxyHook(module, config, lambda **kw: dict(kw))
        hook.install()
        first, second = module.ApplicationBuilder(), module.ApplicationBuilder()
        self.assertEqual(first.requests['api']['connection_pool_size'], 256)
        self.assertEqual(first.requests['updates']['connection_pool_size'], 1)
        for request in first.requests.values():
            self.assertEqual(request['proxy'], config['telegram_proxy_url'])
            self.assertEqual(request['httpx_kwargs'], {'trust_env': False})
        self.assertIsNot(first.requests['api'], second.requests['api'])
        hook.uninstall()

    def test_invalid_never_builds_or_leaks_credentials(self):
        for value in ('', None, 'socks5://user:secret@localhost:1234',
                      'http://user:secret@localhost:99999', 'https://',
                      'http://localhost/path', 'http://localhost?token=secret',
                      'http://localhost\n', 'http://[invalid'):
            with self.subTest(value=value):
                def fail(*args, **kwargs):
                    self.fail('invalid proxy reached builder/request factory')
                module = SimpleNamespace(ApplicationBuilder=fail)
                hook = TelegramProxyHook(module, {'telegram_proxy_enable': True,
                                                 'telegram_proxy_url': value}, fail)
                hook.install()
                with self.assertRaises(ValueError) as raised:
                    module.ApplicationBuilder()
                self.assertNotIn('secret', str(raised.exception))
                hook.uninstall()

    def test_https_auth_and_ipv6(self):
        for value in ('https://user:password@localhost:1234', 'http://[::1]:7897'):
            self.assertEqual(validate_proxy_url(value), value)

    def test_constructor_error_is_sanitized(self):
        def fail(**kwargs):
            raise RuntimeError('http://user:secret@host')
        module = SimpleNamespace(ApplicationBuilder=Builder)
        hook = TelegramProxyHook(module, {'telegram_proxy_enable': True,
                                         'telegram_proxy_url': 'http://localhost'}, fail)
        hook.install()
        with self.assertRaises(ValueError) as raised:
            module.ApplicationBuilder()
        self.assertNotIn('secret', str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        hook.uninstall()

    def test_duplicate_and_third_party_wrapper_reload(self):
        module = SimpleNamespace(ApplicationBuilder=Builder)
        config = {'telegram_proxy_enable': True, 'telegram_proxy_url': 'http://localhost'}
        requests = []
        def factory(**kwargs):
            requests.append(kwargs)
            return kwargs
        first = TelegramProxyHook(module, config, factory)
        first.install()
        wrapped = module.ApplicationBuilder
        first.install()
        duplicate = TelegramProxyHook(module, config, factory)
        duplicate.install()
        self.assertIs(module.ApplicationBuilder, wrapped)
        duplicate.uninstall()
        @wraps(wrapped)
        def third_party(*args, **kwargs):
            return wrapped(*args, **kwargs)
        module.ApplicationBuilder = third_party
        first.uninstall()
        self.assertIs(module.ApplicationBuilder, third_party)
        self.assertEqual(module.ApplicationBuilder().requests, {})
        replacement = TelegramProxyHook(module, config, factory)
        replacement.install()
        module.ApplicationBuilder()
        self.assertEqual(len(requests), 2)
        replacement.uninstall()
        self.assertIs(module.ApplicationBuilder, third_party)


if __name__ == '__main__':
    unittest.main()
