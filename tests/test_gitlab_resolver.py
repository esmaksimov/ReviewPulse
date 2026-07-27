from typing import Any

from reviewpulse.gitlab.resolver import (
    evaluate_reviewer,
    parse_discussions,
    snapshot_from_payloads,
)
from reviewpulse.parsing.gitlab_url import parse_merge_request_url

REF = parse_merge_request_url(
    "https://git.example.com/backend/services/api_controller/-/merge_requests/1112"
)
OTHER_REF = parse_merge_request_url(
    "https://git.example.com/backend/packages/utils/-/merge_requests/223"
)


def thread(
    discussion_id: str,
    author: str,
    resolved: bool,
    created_at: str = "2026-07-27T09:00:00.000Z",
    resolved_at: str | None = "2026-07-27T12:00:00.000Z",
) -> dict[str, Any]:
    return {
        "id": discussion_id,
        "individual_note": False,
        "notes": [
            {
                "id": 1,
                "type": "DiffNote",
                "body": "тут бы вынести в отдельный метод",
                "author": {"id": 10, "username": author},
                "created_at": created_at,
                "resolvable": True,
                "resolved": resolved,
                "resolved_at": resolved_at if resolved else None,
            }
        ],
    }


def system_note(discussion_id: str) -> dict[str, Any]:
    return {
        "id": discussion_id,
        "notes": [
            {
                "id": 2,
                "system": True,
                "body": "changed the description",
                "author": {"username": "someone"},
                "resolvable": False,
                "resolved": False,
            }
        ],
    }


def plain_comment(discussion_id: str, author: str) -> dict[str, Any]:
    """A general MR comment: not attached to a diff, so not resolvable."""
    return {
        "id": discussion_id,
        "individual_note": True,
        "notes": [
            {
                "id": 3,
                "type": None,
                "body": "погнали",
                "author": {"username": author},
                "resolvable": False,
                "resolved": False,
            }
        ],
    }


def snapshot(ref, discussions, blocking: bool | None = None):
    merge_request = {} if blocking is None else {"blocking_discussions_resolved": blocking}
    return snapshot_from_payloads(ref, merge_request, discussions)


def test_ignores_system_notes_and_non_resolvable_comments() -> None:
    threads = parse_discussions(
        REF, [system_note("a"), plain_comment("b", "user1"), thread("c", "user1", True)]
    )
    assert [item.discussion_id for item in threads] == ["c"]


def test_thread_belongs_to_whoever_opened_it() -> None:
    snap = snapshot(REF, [thread("a", "user1", True), thread("b", "user2", False)])

    assert snap.for_reviewer("user1").all_resolved
    assert not snap.for_reviewer("user2").all_resolved
    assert not snap.for_reviewer("someone_else").has_threads


def test_all_threads_resolved_means_feedback_addressed() -> None:
    snap = snapshot(REF, [thread("a", "user1", True), thread("b", "user1", True)])
    status = evaluate_reviewer([snap], "user1")

    assert status.known
    assert status.addressed
    assert not status.has_open_feedback


def test_one_open_thread_keeps_the_ball_on_the_author() -> None:
    snap = snapshot(REF, [thread("a", "user1", True), thread("b", "user1", False)])
    status = evaluate_reviewer([snap], "user1")

    assert not status.addressed
    assert status.has_open_feedback, "this is what silences the nudges"


def test_a_new_unresolved_thread_reopens_the_feedback() -> None:
    """The reviewer looked at the fixes and asked for more — bot must go quiet."""
    resolved_round_one = [thread("a", "user1", True)]
    assert evaluate_reviewer([snapshot(REF, resolved_round_one)], "user1").addressed

    round_two = [
        *resolved_round_one,
        thread("b", "user1", False, created_at="2026-07-27T15:00:00.000Z"),
    ]
    status = evaluate_reviewer([snapshot(REF, round_two)], "user1")
    assert not status.addressed
    assert status.has_open_feedback


def test_every_merge_request_of_the_review_must_be_clean() -> None:
    clean = snapshot(REF, [thread("a", "user1", True)])
    dirty = snapshot(OTHER_REF, [thread("b", "user1", False)])

    assert evaluate_reviewer([clean, dirty], "user1").addressed is False
    assert evaluate_reviewer([clean, dirty], "user1").has_open_feedback


def test_another_reviewers_open_thread_does_not_hold_us_back() -> None:
    snap = snapshot(REF, [thread("a", "user1", True), thread("b", "user2", False)])
    assert evaluate_reviewer([snap], "user1").addressed


def test_reviewer_with_no_threads_is_reported_as_unknown() -> None:
    """They left ✍️ but commented nowhere we can see — don't guess, stay quiet."""
    snap = snapshot(REF, [thread("a", "user2", False)], blocking=True)
    status = evaluate_reviewer([snap], "user1")

    assert not status.known
    assert not status.addressed


def test_falls_back_to_the_mr_wide_flag_without_a_gitlab_mapping() -> None:
    resolved = snapshot(REF, [thread("a", "user2", True)], blocking=True)
    assert evaluate_reviewer([resolved], None).addressed

    open_one = snapshot(REF, [thread("a", "user2", False)], blocking=False)
    assert not evaluate_reviewer([open_one], None).addressed
    assert evaluate_reviewer([open_one], None).has_open_feedback


def test_nothing_to_go_on_is_reported_as_unknown() -> None:
    assert not evaluate_reviewer([], "user1").known
    assert not evaluate_reviewer([snapshot(REF, [])], None).known


def test_usernames_compare_case_insensitively_and_ignore_the_at_sign() -> None:
    snap = snapshot(REF, [thread("a", "User1", True)])
    assert evaluate_reviewer([snap], "@user1").addressed


def test_multi_note_thread_is_open_until_every_note_resolves() -> None:
    discussion = thread("a", "user1", True)
    discussion["notes"].append(
        {
            "id": 9,
            "type": "DiffNote",
            "author": {"username": "user1"},
            "resolvable": True,
            "resolved": False,
            "resolved_at": None,
        }
    )
    snap = snapshot(REF, [discussion])
    assert not snap.for_reviewer("user1").all_resolved


def test_resolution_time_is_the_latest_across_threads() -> None:
    snap = snapshot(
        REF,
        [
            thread("a", "user1", True, resolved_at="2026-07-27T10:00:00.000Z"),
            thread("b", "user1", True, resolved_at="2026-07-27T14:30:00.000Z"),
        ],
    )
    latest = snap.for_reviewer("user1").last_resolved_at
    assert latest is not None
    assert latest.hour == 14 and latest.minute == 30
