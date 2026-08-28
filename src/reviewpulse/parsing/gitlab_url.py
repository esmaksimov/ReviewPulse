"""Extract merge/pull-request coordinates from the links people paste in posts.

GitLab and GitHub use different URL shapes for the same idea (a GitLab merge request
sits at `.../-/merge_requests/<n>`, a GitHub pull request at `.../pull/<n>`), so
`MergeRequestRef` carries a `platform` field derived from whichever shape matched —
that's what lets `web_url` (and everything downstream that renders one back out)
reconstruct the right kind of link instead of always assuming GitLab.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

#: https://git.example.com/backend/services/api_controller/-/merge_requests/1112
#: The project path is everything between the host and the `/-/` separator.
_GITLAB_MR_PATH = re.compile(r"^/(?P<project>.+?)/-/merge_requests/(?P<iid>\d+)")

#: https://github.com/owner/repo/pull/1112 — always exactly two path segments before
#: "pull", unlike GitLab's arbitrary group/subgroup nesting.
_GITHUB_PR_PATH = re.compile(r"^/(?P<project>[^/]+/[^/]+)/pull/(?P<iid>\d+)")

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')]+")

Platform = Literal["gitlab", "github"]


class MergeRequestRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str = Field(min_length=1)
    project_path: str = Field(min_length=1)
    iid: int = Field(gt=0)
    platform: Platform = "gitlab"

    @property
    def encoded_project(self) -> str:
        """GitLab's REST API wants the project path URL-encoded, slashes included."""
        return quote(self.project_path, safe="")

    @property
    def web_url(self) -> str:
        if self.platform == "github":
            return f"https://{self.host}/{self.project_path}/pull/{self.iid}"
        return f"https://{self.host}/{self.project_path}/-/merge_requests/{self.iid}"

    @property
    def short(self) -> str:
        name = self.project_path.rsplit("/", 1)[-1]
        separator = "#" if self.platform == "github" else "!"
        return f"{name}{separator}{self.iid}"


def parse_merge_request_url(url: str) -> MergeRequestRef | None:
    """`None` for anything that isn't a real reference — including a URL that matches
    the shape but not the substance, like `iid=0` (neither GitLab nor GitHub ever
    issues that one; someone's placeholder test link, most likely). Letting
    `MergeRequestRef`'s own validation raise here would take the one bad link's
    whole caller down with it — `find_merge_requests` would lose every *other* MR it
    had already found in the same post, and in `/announce` the composer's message
    would just go unanswered."""
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return None

    for platform, pattern in (("gitlab", _GITLAB_MR_PATH), ("github", _GITHUB_PR_PATH)):
        match = pattern.match(parts.path)
        if match:
            try:
                return MergeRequestRef(
                    host=parts.netloc,
                    project_path=match.group("project").strip("/"),
                    iid=int(match.group("iid")),
                    platform=platform,
                )
            except ValidationError:
                return None
    return None


def find_merge_requests(text: str) -> list[MergeRequestRef]:
    """All distinct MR/PR links in a post, GitLab and GitHub alike, in order of
    appearance.

    Posts routinely carry more than one ("MR API:" plus "MR Utils:"), and a review is
    only "fixed" once every one of them is clean — so we keep them all.
    """
    seen: dict[tuple[str, str, int], MergeRequestRef] = {}
    for raw in _URL_IN_TEXT.findall(text):
        ref = parse_merge_request_url(raw.rstrip(".,;)"))
        if ref is not None:
            seen.setdefault((ref.host, ref.project_path, ref.iid), ref)
    return list(seen.values())
