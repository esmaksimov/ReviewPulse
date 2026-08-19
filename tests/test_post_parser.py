from dataclasses import dataclass

from reviewpulse.parsing.gitlab_url import find_merge_requests, parse_merge_request_url
from reviewpulse.parsing.post_parser import parse_post

# How posts actually look in practice: "Ревью:" rather than the template's
# "Ревьювер:", several MRs on separate labelled lines, and a task-tracker link that
# must not be mistaken for one of them.
REAL_POST = """Платежи

Доработка connection pool

MR SC: https://git.example.com/backend/services/api_controller/-/merge_requests/1112

MR Utils: https://git.example.com/backend/packages/utils/-/merge_requests/223

Задача: https://tasks.example.com/space/2829/boards/card/3517380

Ревью: @user1 @user2"""

# The strict template a team might pin: one "MR:" line, docs instead of a task, and
# prose mixed in with the handles.
TEMPLATE_POST = """Каталог
Оплата картой: Починить редирект

MR: https://git.example.com/backend/services/checkout/-/merge_requests/77

Документация: https://wiki.example.com/pages/12345
Описание: Если документация отсутствует
Ревьювер: @user2 для бэка / @user1 для остальных, @user3"""


def test_parses_the_real_post() -> None:
    post = parse_post(REAL_POST)

    assert post.product == "Платежи"
    assert post.title == "Доработка connection pool"
    assert post.task_url == "https://tasks.example.com/space/2829/boards/card/3517380"
    assert [mention.username for mention in post.reviewers] == ["user1", "user2"]
    assert post.looks_like_review


def test_keeps_every_merge_request_from_the_post() -> None:
    """A review is only "fixed" when all of its MRs are, so none may be dropped."""
    post = parse_post(REAL_POST)

    assert [(mr.project_path, mr.iid) for mr in post.merge_requests] == [
        ("backend/services/api_controller", 1112),
        ("backend/packages/utils", 223),
    ]
    assert all(mr.host == "git.example.com" for mr in post.merge_requests)


def test_task_link_is_not_treated_as_a_merge_request() -> None:
    assert find_merge_requests("https://tasks.example.com/space/2829/boards/card/3517380") == []


def test_parses_the_pinned_template() -> None:
    post = parse_post(TEMPLATE_POST)

    assert post.product == "Каталог"
    assert post.title == "Оплата картой: Починить редирект"
    assert post.docs_url == "https://wiki.example.com/pages/12345"
    assert len(post.merge_requests) == 1
    assert [mention.username for mention in post.reviewers] == [
        "user2",
        "user1",
        "user3",
    ], "prose on the reviewer line must not hide the handles"


def test_falls_back_to_any_handle_when_the_reviewer_line_is_missing() -> None:
    post = parse_post(
        "Каталог\nРефакторинг\n\n"
        "https://git.example.com/backend/services/api/-/merge_requests/9\n\n"
        "@user1 @user2 посмотрите пожалуйста"
    )
    assert [mention.username for mention in post.reviewers] == ["user1", "user2"]


def test_post_without_a_merge_request_is_not_a_review() -> None:
    """Announcements and chatter in the channel must be ignored, not tracked."""
    post = parse_post("Всем привет, завтра релиз в 15:00. @user1 имей в виду")
    assert not post.looks_like_review


def test_a_labelled_reviewer_line_makes_a_post_a_review_even_without_an_mr() -> None:
    """A real post from the channel: an infra/docs-only change with no code MR at
    all, tracked by wiki link and a "Дока:" (colloquial short for "Документация")
    label. It still names reviewers deliberately, so it must be tracked."""
    post = parse_post(
        "Продукт\n\n"
        "Вынос телефона в отдельное хранилище\n\n"
        "Дока: https://wiki.example.com/spaces/TEAM/pages/2037240524/API\n\n"
        "Ревьюверы: @user1, @user2"
    )

    assert post.docs_url == "https://wiki.example.com/spaces/TEAM/pages/2037240524/API"
    assert [mention.username for mention in post.reviewers] == ["user1", "user2"]
    assert post.merge_requests == []
    assert post.has_labelled_reviewers
    assert post.looks_like_review


def test_stray_handle_without_a_reviewer_label_is_still_not_a_review() -> None:
    """Chatter that happens to @-mention someone, with no MR and no explicit
    reviewer line, must not be picked up just because a fallback scan finds a handle."""
    post = parse_post("Продукт\n\nОбновили конфиг\n\nСпасибо @user1 за помощь")
    assert [mention.username for mention in post.reviewers] == ["user1"], (
        "the fallback scan does find the handle"
    )
    assert not post.has_labelled_reviewers
    assert not post.looks_like_review


def test_malformed_post_never_raises() -> None:
    for text in ("", "\n\n\n", "https://", "@", "Платежи"):
        assert parse_post(text) is not None


def test_duplicate_links_and_handles_are_collapsed() -> None:
    url = "https://git.example.com/group/proj/-/merge_requests/5"
    post = parse_post(f"Платежи\nФикс\n\nMR: {url}\nДубль: {url}\n\nРевью: @user1 @user1")
    assert len(post.merge_requests) == 1
    assert [mention.username for mention in post.reviewers] == ["user1"]


def test_too_short_handles_are_not_telegram_usernames() -> None:
    """Telegram requires 5+ characters, so "@bob" in prose is not a reviewer."""
    post = parse_post("Платежи\nФикс\n\nMR: https://git.example.com/g/p/-/merge_requests/1\n\n@bob")
    assert post.reviewers == []


def test_trailing_punctuation_is_stripped_from_links() -> None:
    refs = find_merge_requests("см. https://git.example.com/group/proj/-/merge_requests/5.")
    assert refs[0].iid == 5


def test_project_path_is_url_encoded_for_the_api() -> None:
    ref = parse_merge_request_url("https://git.example.com/a/b/c/-/merge_requests/12")
    assert ref is not None
    assert ref.encoded_project == "a%2Fb%2Fc"
    assert ref.short == "c!12"


def test_text_mentions_supply_the_user_id_for_handleless_reviewers() -> None:
    """Users without a @username can only be reached via the entity's user id."""

    @dataclass
    class FakeUser:
        id: int
        username: str | None
        full_name: str

    @dataclass
    class FakeEntity:
        type: str
        user: FakeUser

    post = parse_post(
        "Платежи\nФикс\n\nMR: https://git.example.com/g/p/-/merge_requests/1\n\nРевью: Иван Петров",
        entities=[FakeEntity("text_mention", FakeUser(id=555, username=None, full_name="Иван"))],
    )
    assert [(m.user_id, m.username) for m in post.reviewers] == [(555, None)]
    assert post.reviewers[0].key == "id:555"


def test_english_labels_are_recognized() -> None:
    post = parse_post(
        "Payments\nConnection pool rework\n\n"
        "MR: https://git.example.com/backend/api/-/merge_requests/1112\n\n"
        "Docs: https://wiki.example.com/pages/1\n\n"
        "Task: https://tasks.example.com/card/1\n\n"
        "Review: @user1 @user2"
    )
    assert post.title == "Connection pool rework"
    assert post.docs_url == "https://wiki.example.com/pages/1"
    assert post.task_url == "https://tasks.example.com/card/1"
    assert [mention.username for mention in post.reviewers] == ["user1", "user2"]


def test_spanish_labels_are_recognized() -> None:
    post = parse_post(
        "Pagos\nMejora del connection pool\n\n"
        "MR: https://git.example.com/backend/api/-/merge_requests/1112\n\n"
        "Documentación: https://wiki.example.com/pages/1\n"
        "Descripción: si falta la documentación\n\n"
        "Revisión: @user1 @user2"
    )
    assert post.title == "Mejora del connection pool"
    assert post.docs_url == "https://wiki.example.com/pages/1"
    assert [mention.username for mention in post.reviewers] == ["user1", "user2"]


def test_italian_labels_are_recognized() -> None:
    post = parse_post(
        "Pagamenti\nMiglioramento del connection pool\n\n"
        "MR: https://git.example.com/backend/api/-/merge_requests/1112\n\n"
        "Documentazione: https://wiki.example.com/pages/1\n"
        "Descrizione: se manca la documentazione\n\n"
        "Revisori: @user1 @user2"
    )
    assert post.title == "Miglioramento del connection pool"
    assert post.docs_url == "https://wiki.example.com/pages/1"
    assert [mention.username for mention in post.reviewers] == ["user1", "user2"]


def test_chinese_labels_are_recognized() -> None:
    """Chinese posts commonly use a fullwidth colon (：) after the label."""
    post = parse_post(
        "支付\n连接池优化\n\n"
        "MR：https://git.example.com/backend/api/-/merge_requests/1112\n\n"
        "文档：https://wiki.example.com/pages/1\n\n"
        "评审：@user1 @user2"
    )
    assert post.title == "连接池优化"
    assert post.docs_url == "https://wiki.example.com/pages/1"
    assert [mention.username for mention in post.reviewers] == ["user1", "user2"]


def test_an_author_line_is_parsed_opt_in() -> None:
    post = parse_post(REAL_POST + "\n\nАвтор: @poster")
    assert post.author is not None
    assert post.author.username == "poster"


def test_no_author_line_leaves_the_author_unresolved() -> None:
    post = parse_post(REAL_POST)
    assert post.author is None


def test_an_author_line_without_a_handle_is_not_guessed_at() -> None:
    """A bare name gives us nothing to DM — better to leave it unresolved than to
    misattribute the review to whoever happens to be mentioned elsewhere in it."""
    post = parse_post(REAL_POST + "\n\nАвтор: Иван Иванов")
    assert post.author is None


def test_english_author_label_is_recognized() -> None:
    post = parse_post(
        "Payments\nConnection pool rework\n\n"
        "MR: https://git.example.com/backend/api/-/merge_requests/1112\n\n"
        "Review: @user1 @user2\n\n"
        "Author: @poster"
    )
    assert post.author is not None
    assert post.author.username == "poster"


def test_a_handle_and_its_text_mention_are_merged_into_one_reviewer() -> None:
    @dataclass
    class FakeUser:
        id: int
        username: str | None
        full_name: str

    @dataclass
    class FakeEntity:
        type: str
        user: FakeUser

    post = parse_post(
        "Платежи\nФикс\n\nMR: https://git.example.com/g/p/-/merge_requests/1\n\nРевью: @user1",
        entities=[FakeEntity("text_mention", FakeUser(id=7, username="user1", full_name="A"))],
    )
    assert len(post.reviewers) == 1
    assert post.reviewers[0].user_id == 7
    assert post.reviewers[0].username == "user1"
