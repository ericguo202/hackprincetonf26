"""The only way to call Matterport. Read-only: any GraphQL mutation is rejected.

Matterport keys are admin keys that can buy things (AGENTS.md rule 9). Server side only.
Lane D owns this file.
"""

from __future__ import annotations

import os
import re

import httpx

ENDPOINT = "https://api.matterport.com/api/models/graph"

# Strip comments and string literals, then look for a mutation/subscription operation.
_COMMENT = re.compile(r"#[^\n]*")
_STRING = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"')
_FORBIDDEN_OP = re.compile(r"(^|[\s{}])(mutation|subscription)\b", re.IGNORECASE)


class MutationRejected(ValueError):
    pass


def assert_read_only(query: str) -> None:
    cleaned = _COMMENT.sub("", _STRING.sub('""', query))
    if _FORBIDDEN_OP.search(cleaned):
        raise MutationRejected("Matterport client is read-only; mutations are not allowed")


class MatterportClient:
    def __init__(self, token_id: str | None = None, token_secret: str | None = None) -> None:
        self._auth = (
            token_id or os.environ["MATTERPORT_API_TOKEN_ID"],
            token_secret or os.environ["MATTERPORT_API_TOKEN_SECRET"],
        )

    def query(self, query: str, variables: dict | None = None) -> dict:
        assert_read_only(query)
        resp = httpx.post(
            ENDPOINT, json={"query": query, "variables": variables or {}}, auth=self._auth, timeout=30
        )
        resp.raise_for_status()
        return resp.json()
