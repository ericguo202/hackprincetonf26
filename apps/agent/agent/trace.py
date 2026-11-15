"""LangSmith trace links for agent runs (AGENTS.md section 10: "the trace link shows in the UI").

Each turn gets a run_id we choose (config["run_id"] names the root run), so the link is known before the
run ends. The link is {web host}/o/{tenant}/projects/p/{project}/r/{run_id}?poll=true; the prefix up to
the project needs one or two LangSmith requests, so it is resolved once per process and cached. The
destination follows the SDK's own rule: LANGSMITH_AGENT_ID + LANGSMITH_AGENT_ENVIRONMENT (agent-based
workspace), else LANGSMITH_PROJECT. With tracing off or LangSmith unreachable the link is None and the
turn runs as usual.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import warnings
from types import SimpleNamespace

log = logging.getLogger("agent")

_prefix: str | None = None  # "{host}/o/{tenant}/projects/p/{project}"
_failed = False


def enabled() -> bool:
    try:
        from langsmith import utils

        return bool(utils.tracing_is_enabled()) and bool(utils.get_env_var("API_KEY"))
    except Exception:
        return False


def _resolve_prefix() -> str:
    from langsmith import Client
    from langsmith.address import address_from_env

    probe = uuid.uuid4()
    with warnings.catch_warnings():  # get_run_url is deprecated in favour of runs.get_url (needs trace_id)
        warnings.simplefilter("ignore")
        url = Client().get_run_url(run=SimpleNamespace(id=probe), address=address_from_env())
    return url.split(f"/r/{probe}")[0]


def url_for(run_id: uuid.UUID | str, prefix: str) -> str:
    return f"{prefix}/r/{run_id}?poll=true"


async def trace_url(run_id: uuid.UUID | str, timeout: float = 5.0) -> str | None:
    """The LangSmith link for a root run, or None when tracing is off or LangSmith cannot be reached."""
    global _prefix, _failed
    if not enabled() or _failed:
        return None
    if _prefix is None:
        try:
            _prefix = await asyncio.wait_for(asyncio.to_thread(_resolve_prefix), timeout)
        except Exception as e:  # a bad key or no network must not break the chat
            log.warning("no LangSmith trace link: %s", e)
            _failed = True  # do not retry (and wait) on every turn
            return None
    return url_for(run_id, _prefix)
