"""Git settings for processes that run outside the agent's sandbox (exec scripts, the worker's own git calls).

An agent can write inside the repo's .git, so its hooks and config are not trusted: no system config, no
hooks, no fsmonitor program. Everything else (user config, signing) is left alone."""

GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "core.hooksPath",
    "GIT_CONFIG_VALUE_0": "/dev/null",
    "GIT_CONFIG_KEY_1": "core.fsmonitor",
    "GIT_CONFIG_VALUE_1": "",
}
