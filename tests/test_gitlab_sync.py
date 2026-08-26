"""GitLab sync over a mocked HTTP layer: does polling move the state machine?"""

from __future__ import annotations

import httpx
import pytest
import respx

from conftest import msk
from reviewpulse.db import repo
from reviewpulse.domain.state import Event, ReviewerState
from reviewpulse.gitlab.client import GitLabClient, GitLabError
from reviewpulse.parsing.post_parser import parse_post
from reviewpulse.services import gitlab_sync
from reviewpulse.services import reviews as review_service

BASE = "https://git.example.com"
PROJECT = "backend%2Fservices%2Fapi_controller"
POST = (
    "Платежи\n\nДоработка connection pool\n\n"
    "MR: https://git.example.com/backend/services/api_controller/-/merge_requests/1112\n\n"
    "Ревью: @user1 @user2"
)


def thread(author: str, resolved: bool) -> dict:
    return {
        "id": f"{author}-{resolved}",
        "notes": [
            {
                "id": 1,
                "type": "DiffNote",
                "author": {"username": author},
                "created_at": "2026-07-27T09:00:00.000Z",
                "resolvable": True,
                "resolved": resolved,
                "resolved_at": "2026-07-27T11:00:00.000Z" if resolved else None,
            }
        ],
    }


def mock_gitlab(discussions: list[dict], blocking: bool = True) -> None:
    respx.get(f"{BASE}/api/v4/projects/{PROJECT}/merge_requests/1112").mock(
        return_value=httpx.Response(200, json={"blocking_discussions_resolved": blocking})
    )
    respx.get(f"{BASE}/api/v4/projects/{PROJECT}/merge_requests/1112/discussions").mock(
        return_value=httpx.Response(200, json=discussions)
    )


async def setup_review(session, *, with_gitlab_logins: bool = True):
    review = await review_service.create_or_update_review(
        session,
        channel_chat_id=-100,
        channel_message_id=1,
        post=parse_post(POST),
        raw_text=POST,
        posted_at=msk(27, 9, 0),
    )
    for username, telegram_id in (("user1", 101), ("user2", 102)):
        user = await repo.upsert_user(session, telegram_id, username=username)
        if with_gitlab_logins:
            user.gitlab_username = username
        await review_service.link_user_to_assignments(session, telegram_id, username)
    await session.flush()
    return review


async def run_sync(session):
    async with GitLabClient(base_url=BASE, token="t", max_retries=1) as client:
        return await gitlab_sync.sync_open_reviews(session, client, now=msk(27, 12, 0))


@pytest.fixture
def gitlab_mock():
    """The global router, so the module-level `respx.get(...)` helpers register on it."""
    with respx.mock:
        yield respx.mock


async def test_resolved_threads_hand_the_ball_back_to_the_reviewer(
    session, gitlab_mock
) -> None:
    review = await setup_review(session)
    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 10, 0))

    mock_gitlab([thread("user1", resolved=True)])
    changes = await run_sync(session)

    assert [change.event for change in changes] == [Event.FIXES_DONE]
    assert reviewer.state is ReviewerState.AWAITING_RECHECK


async def test_an_open_thread_leaves_the_ball_with_the_author(session, gitlab_mock) -> None:
    review = await setup_review(session)
    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 10, 0))

    mock_gitlab([thread("user1", resolved=False)], blocking=False)
    changes = await run_sync(session)

    assert changes == []
    assert reviewer.state is ReviewerState.CHANGES_REQUESTED


async def test_a_new_thread_after_the_fixes_silences_the_nudges(session, gitlab_mock) -> None:
    """Reviewer looked at the fixes and asked for more — the bot must back off."""
    review = await setup_review(session)
    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 10, 0))
    await review_service.apply_verdict(session, reviewer, Event.FIXES_DONE, msk(27, 11, 0))
    assert reviewer.state is ReviewerState.AWAITING_RECHECK

    mock_gitlab([thread("user1", resolved=True), thread("user1", resolved=False)])
    changes = await run_sync(session)

    assert [change.event for change in changes] == [Event.REQUEST_CHANGES]
    assert reviewer.state is ReviewerState.CHANGES_REQUESTED


async def test_another_reviewers_open_thread_does_not_block_this_one(
    session, gitlab_mock
) -> None:
    review = await setup_review(session)
    first = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, first, Event.REQUEST_CHANGES, msk(27, 10, 0))

    mock_gitlab([thread("user1", resolved=True), thread("user2", resolved=False)])
    await run_sync(session)

    assert first.state is ReviewerState.AWAITING_RECHECK


async def test_approved_reviewers_are_never_touched_by_the_sync(session, gitlab_mock) -> None:
    """A thread reopened by anyone must not silently revoke a 👍."""
    review = await setup_review(session)
    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.APPROVE, msk(27, 10, 0))

    mock_gitlab([thread("user1", resolved=False)], blocking=False)
    changes = await run_sync(session)

    assert changes == []
    assert reviewer.state is ReviewerState.APPROVED


async def test_reviewer_without_a_gitlab_login_falls_back_to_the_mr_flag(
    session, gitlab_mock
) -> None:
    review = await setup_review(session, with_gitlab_logins=False)
    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 10, 0))

    mock_gitlab([thread("someone_else", resolved=True)], blocking=True)
    await run_sync(session)

    assert reviewer.state is ReviewerState.AWAITING_RECHECK


async def test_a_reviewer_who_left_no_threads_is_left_alone(session, gitlab_mock) -> None:
    """They pressed ✍️ but commented outside the diff — don't guess."""
    review = await setup_review(session)
    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 10, 0))

    mock_gitlab([thread("user2", resolved=True)], blocking=True)
    changes = await run_sync(session)

    assert changes == []
    assert reviewer.state is ReviewerState.CHANGES_REQUESTED


async def test_closed_reviews_are_not_polled(session, gitlab_mock) -> None:
    review = await setup_review(session)
    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 10, 0))
    await review_service.close_review(session, review)

    route = respx.get(f"{BASE}/api/v4/projects/{PROJECT}/merge_requests/1112")
    await run_sync(session)

    assert not route.called, "closed reviews must not spend API calls"


async def test_a_github_linked_mr_is_never_polled_through_the_gitlab_client(
    session, gitlab_mock
) -> None:
    """This poller only ever speaks the GitLab REST API — a GitHub PR link on the
    same review must be skipped outright, not sent to the GitLab client (which would
    build a nonsense URL and, here, trip respx's unmocked-request guard)."""
    post_text = POST + "\nPR: https://github.com/example-org/example-repo/pull/9"
    review = await review_service.create_or_update_review(
        session,
        channel_chat_id=-100,
        channel_message_id=1,
        post=parse_post(post_text),
        raw_text=post_text,
        posted_at=msk(27, 9, 0),
    )
    for username, telegram_id in (("user1", 101), ("user2", 102)):
        await repo.upsert_user(session, telegram_id, username=username, full_name=None)
        (await repo.get_user_by_telegram_id(session, telegram_id)).gitlab_username = username
        await review_service.link_user_to_assignments(session, telegram_id, username)

    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 10, 0))
    assert {link.platform for link in review.merge_requests} == {"gitlab", "github"}

    mock_gitlab([thread("user1", resolved=True)])
    changes = await run_sync(session)

    assert [change.event for change in changes] == [Event.FIXES_DONE]


async def test_reviews_with_nothing_to_move_are_not_polled(session, gitlab_mock) -> None:
    """Everyone is still PENDING: no thread state could change anything."""
    await setup_review(session)

    route = respx.get(f"{BASE}/api/v4/projects/{PROJECT}/merge_requests/1112")
    await run_sync(session)

    assert not route.called


async def test_a_gitlab_outage_leaves_the_state_untouched(session, gitlab_mock) -> None:
    review = await setup_review(session)
    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 10, 0))

    respx.get(f"{BASE}/api/v4/projects/{PROJECT}/merge_requests/1112").mock(
        return_value=httpx.Response(500)
    )
    changes = await run_sync(session)

    assert changes == []
    assert reviewer.state is ReviewerState.CHANGES_REQUESTED
    assert review.merge_requests[0].sync_error is not None


async def test_a_client_error_is_not_retried(session, gitlab_mock) -> None:
    route = respx.get(f"{BASE}/api/v4/projects/{PROJECT}/merge_requests/1112").mock(
        return_value=httpx.Response(404, text="not found")
    )
    async with GitLabClient(base_url=BASE, token="t", max_retries=3) as client:
        from reviewpulse.parsing.gitlab_url import parse_merge_request_url

        ref = parse_merge_request_url(
            "https://git.example.com/backend/services/api_controller/-/merge_requests/1112"
        )
        with pytest.raises(GitLabError):
            await client.get_merge_request(ref)

    assert route.call_count == 1, "a 404 will not fix itself"


async def test_discussions_are_paginated(session, gitlab_mock) -> None:
    from reviewpulse.parsing.gitlab_url import parse_merge_request_url

    url = f"{BASE}/api/v4/projects/{PROJECT}/merge_requests/1112/discussions"
    page_one = [thread(f"user{index}", True) for index in range(100)]
    respx.get(url, params={"page": "1"}).mock(return_value=httpx.Response(200, json=page_one))
    respx.get(url, params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=[thread("last_one", True)])
    )

    ref = parse_merge_request_url(
        "https://git.example.com/backend/services/api_controller/-/merge_requests/1112"
    )
    async with GitLabClient(base_url=BASE, token="t") as client:
        discussions = await client.get_discussions(ref)

    assert len(discussions) == 101


async def test_the_token_is_sent_as_a_private_token_header(session, gitlab_mock) -> None:
    from reviewpulse.parsing.gitlab_url import parse_merge_request_url

    route = respx.get(f"{BASE}/api/v4/projects/{PROJECT}/merge_requests/1112").mock(
        return_value=httpx.Response(200, json={})
    )
    ref = parse_merge_request_url(
        "https://git.example.com/backend/services/api_controller/-/merge_requests/1112"
    )
    async with GitLabClient(base_url=BASE, token="secret-token") as client:
        await client.get_merge_request(ref)

    assert route.calls.last.request.headers["PRIVATE-TOKEN"] == "secret-token"
