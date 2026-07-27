"""Extract GitLab merge-request coordinates from the links people paste in posts."""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

#: https://git.example.com/backend/services/api_controller/-/merge_requests/1112
#: The project path is everything between the host and the `/-/` separator.
_MR_PATH = re.compile(r"^/(?P<project>.+?)/-/merge_requests/(?P<iid>\d+)")

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')]+")


class MergeRequestRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str = Field(min_length=1)
    project_path: str = Field(min_length=1)
    iid: int = Field(gt=0)

    @property
    def encoded_project(self) -> str:
        """GitLab's REST API wants the project path URL-encoded, slashes included."""
        return quote(self.project_path, safe="")

    @property
    def web_url(self) -> str:
        return f"https://{self.host}/{self.project_path}/-/merge_requests/{self.iid}"

    @property
    def short(self) -> str:
        return f"{self.project_path.rsplit('/', 1)[-1]}!{self.iid}"


def parse_merge_request_url(url: str) -> MergeRequestRef | None:
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return None
    match = _MR_PATH.match(parts.path)
    if not match:
        return None
    return MergeRequestRef(
        host=parts.netloc,
        project_path=match.group("project").strip("/"),
        iid=int(match.group("iid")),
    )


def find_merge_requests(text: str) -> list[MergeRequestRef]:
    """All distinct MR links in a post, in order of appearance.

    Posts routinely carry more than one ("MR SC:" plus "MR Utils:"), and a review is
    only "fixed" once every one of them is clean — so we keep them all.
    """
    seen: dict[tuple[str, str, int], MergeRequestRef] = {}
    for raw in _URL_IN_TEXT.findall(text):
        ref = parse_merge_request_url(raw.rstrip(".,;)"))
        if ref is not None:
            seen.setdefault((ref.host, ref.project_path, ref.iid), ref)
    return list(seen.values())
