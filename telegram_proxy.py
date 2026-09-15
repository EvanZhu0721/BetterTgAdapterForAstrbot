"""Module-local Telegram builder hook; existing clients are never replaced."""
from functools import wraps
from urllib.parse import urlsplit

_MARKER = "_astrbot_telegram_independent_proxy_owner"


def validate_proxy_url(value):
    """Validate without including credentials in errors."""
    message = "Telegram independent proxy requires a valid HTTP/HTTPS proxy URL; SOCKS is not supported."
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError(message)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError
        parsed.port  # Validate malformed and out-of-range ports.
    except (ValueError, TypeError):
        raise ValueError(message) from None
    return value


class TelegramProxyHook:
    def __init__(self, module, config, request_factory=None):
        self.module = module
        self.config = config
        self.request_factory = request_factory
        self.active = False
        self.wrapper = None
        self.previous = None

    def install(self):
        current = self.module.ApplicationBuilder
        if current is self.wrapper:
            return
        existing = getattr(current, _MARKER, None)
        if existing is not None and existing.active:
            return
        self.previous = current
        self.active = True
        owner = self

        @wraps(current)
        def builder(*args, **kwargs):
            if not owner.active or not owner.config.get("telegram_proxy_enable", False):
                return current(*args, **kwargs)
            url = validate_proxy_url(owner.config.get("telegram_proxy_url", ""))
            factory = owner.request_factory
            if factory is None:
                from telegram.request import HTTPXRequest
                factory = HTTPXRequest
            # Both constructors are network-free. Never let a constructor's
            # exception expose an authenticated proxy URL in AstrBot logs.
            try:
                request = factory(connection_pool_size=256, proxy=url,
                                  httpx_kwargs={"trust_env": False})
                updates = factory(connection_pool_size=1, proxy=url,
                                  httpx_kwargs={"trust_env": False})
            except Exception:
                raise ValueError("Telegram independent proxy client initialization failed; check proxy configuration and HTTPX compatibility.") from None
            return current(*args, **kwargs).request(request).get_updates_request(updates)

        setattr(builder, _MARKER, self)
        self.wrapper = builder
        self.module.ApplicationBuilder = builder

    def uninstall(self):
        self.active = False
        if self.wrapper is not None and self.module.ApplicationBuilder is self.wrapper:
            self.module.ApplicationBuilder = self.previous
        self.wrapper = None
        self.previous = None
