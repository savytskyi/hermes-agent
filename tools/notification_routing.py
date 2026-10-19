"""Purpose-specific notification destination routing.

Telegram notification aliases resolve through environment variables rather than
hard-coded chat IDs. Bare ``telegram`` remains the interactive/home destination;
automations should use ``telegram:<alias>``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_DIRECTIVE_RE = re.compile(
    r"^\s*\[\[notification_channel:([a-z][a-z0-9_-]*)\]\]\s*(?:\r?\n)?",
    re.IGNORECASE,
)

# Reserved aliases fail closed if their variable is absent. The generic naming
# convention makes new aliases extensible without code changes once their env
# variable exists.
_RESERVED_ALIASES = frozenset(
    {"markets", "home", "home_all", "dev", "media", "critical"}
)


class NotificationRoutingError(ValueError):
    """Raised when a recognized notification alias cannot be resolved safely."""


def normalize_channel_alias(alias: str) -> str:
    """Return the canonical lower-case alias or raise for invalid input."""
    normalized = alias.strip().lower().replace("-", "_").replace(" ", "_")
    if not _ALIAS_RE.fullmatch(normalized):
        raise NotificationRoutingError(f"Invalid Telegram notification channel alias: {alias!r}")
    return normalized


def telegram_channel_env_var(alias: str) -> str:
    """Map ``home_all`` to ``TELEGRAM_HOME_ALL_CHAT_ID`` and so on."""
    return f"TELEGRAM_{normalize_channel_alias(alias).upper()}_CHAT_ID"


def resolve_telegram_notification_alias(
    alias: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str] | None:
    """Resolve a notification alias to ``(chat_id, env_var)``.

    Unknown names return ``None`` so Hermes' existing channel-directory lookup
    remains available. Reserved aliases and aliases whose conventional env var
    is present are treated as notification channels. A recognized alias with an
    empty/missing value raises instead of falling back to the interactive chat.
    """
    try:
        normalized = normalize_channel_alias(alias)
    except NotificationRoutingError:
        return None
    env_var = telegram_channel_env_var(normalized)
    raw_value = _lookup(env_var, environ)
    recognized = normalized in _RESERVED_ALIASES or raw_value is not None
    if not recognized:
        return None
    chat_id = str(raw_value or "").strip()
    if not chat_id:
        raise NotificationRoutingError(
            f"Telegram notification channel '{normalized}' requires {env_var}; "
            "refusing to fall back to the interactive Hermes chat"
        )
    return chat_id, env_var


def _lookup(env_var: str, environ: Mapping[str, str] | None) -> str | None:
    """Read *env_var* from an explicit mapping (tests) or the profile-scoped secret store.

    ``agent.secret_scope.get_secret`` honours the active profile's ``.env`` under the
    multiplexing gateway and reads ``os.environ`` in single-profile deployments."""
    if environ is not None:
        return environ.get(env_var)
    from agent.secret_scope import UnscopedSecretError, get_secret
    try:
        return get_secret(env_var)
    except UnscopedSecretError:
        # Multiplexing with no profile scope: never read another profile's value. Reserved
        # aliases then fail closed in the caller; unknown names fall through to the directory.
        return None


def notification_channel_directive(alias: str) -> str:
    """Return a control line scripts may prepend for per-result routing."""
    return f"[[notification_channel:{normalize_channel_alias(alias)}]]"


def extract_notification_channel_directive(text: str) -> tuple[str, str | None]:
    """Strip and return a leading per-result channel directive."""
    match = _DIRECTIVE_RE.match(text or "")
    if not match:
        return text, None
    alias = normalize_channel_alias(match.group(1))
    return text[match.end() :], alias
