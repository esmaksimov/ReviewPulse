"""Minimal read-only GitLab REST client.

Only two endpoints matter: the MR itself (for the cheap `blocking_discussions_resolved`
signal) and its discussions (for per-reviewer thread state).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from ..parsing.gitlab_url import MergeRequestRef

logger = logging.getLogger(__name__)

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_PER_PAGE = 100


class GitLabError(Exception):
    pass


class GitLabClient(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    base_url: str = Field(min_length=1)
    token: str = Field(min_length=1, repr=False)
    timeout: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=3, ge=1)

    _client: httpx.AsyncClient | None = PrivateAttr(default=None)

    async def __aenter__(self) -> GitLabClient:
        self._client = httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            headers={"PRIVATE-TOKEN": self.token},
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise GitLabError("GitLabClient used outside its async context")
        return self._client

    async def get_merge_request(self, ref: MergeRequestRef) -> dict[str, Any]:
        return await self._get(
            f"/api/v4/projects/{ref.encoded_project}/merge_requests/{ref.iid}"
        )

    async def get_discussions(self, ref: MergeRequestRef) -> list[dict[str, Any]]:
        """All discussion threads on an MR, following pagination."""
        discussions: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = await self._get(
                f"/api/v4/projects/{ref.encoded_project}/merge_requests/{ref.iid}/discussions",
                params={"per_page": _PER_PAGE, "page": page},
            )
            if not isinstance(batch, list):
                raise GitLabError(f"unexpected discussions payload for {ref.short}")
            discussions.extend(batch)
            if len(batch) < _PER_PAGE:
                return discussions
            page += 1

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    return response.json()
                if response.status_code not in _RETRY_STATUSES:
                    raise GitLabError(
                        f"GET {path} -> {response.status_code}: {response.text[:200]}"
                    )
                last_error = GitLabError(f"GET {path} -> {response.status_code}")

            if attempt < self.max_retries - 1:
                await asyncio.sleep(2**attempt)

        raise GitLabError(f"GET {path} failed after {self.max_retries} attempts") from last_error
