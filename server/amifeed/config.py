"""Settings from a TOML file, with secrets also from files or the environment."""

import ipaddress
import os
import tomllib

DEFAULTS = {
    "server": {
        "listen": "0.0.0.0",
        "port": 8642,
        "allow_nets": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                       "::1/128", "fc00::/7", "fe80::/10"],
    },
    "greader": {"url": "", "user": ""},
    "fetch": {"timeout": 10, "max_bytes": 2 << 20, "allow_private_feeds": False,
              "user_agent": "amifeed/1.0 (+https://github.com/)"},
    "cache": {"path": "amifeed-cache.sqlite", "ttl_days": 7, "max_mb": 50, "local_feed_ttl": 900},
    "text": {"fulltext_feeds": [], "excerpt_chars": 400},
}


class ConfigError(Exception):
    pass


def _secret(section, name, env, base):
    """A secret comes from the environment, a *_file setting (relative to the
    settings file) or the setting itself."""
    if os.environ.get(env):
        return os.environ[env].strip()
    path = section.get(name + "_file")
    if path:
        with open(os.path.join(base, os.path.expanduser(path)), encoding="utf-8") as f:
            return f.read().strip()
    return str(section.get(name, "")).strip()


def load(path):
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cfg = {k: dict(v, **raw.get(k, {})) for k, v in DEFAULTS.items()}
    base = os.path.dirname(os.path.abspath(path))
    cfg["server"]["token"] = _secret(raw.get("server", {}), "token", "AMIFEED_TOKEN", base)
    cfg["greader"]["password"] = _secret(raw.get("greader", {}), "password",
                                         "AMIFEED_GREADER_PASSWORD", base)
    if len(cfg["server"]["token"]) < 12:
        raise ConfigError("server.token must be set and at least 12 characters long")
    for key in ("url", "user", "password"):
        if not cfg["greader"][key]:
            raise ConfigError("greader.%s must be set" % key)
    try:
        cfg["server"]["allow_nets"] = [ipaddress.ip_network(n) for n in cfg["server"]["allow_nets"]]
    except ValueError as e:
        raise ConfigError("server.allow_nets: %s" % e)
    return cfg
