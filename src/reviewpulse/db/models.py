"""Persistence model.

Shape mirrors the domain: a Review is one post in the channel, and it fans out into one
ReviewerAssignment per named reviewer — that pair is what carries state and gets nudged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from ..domain.state import ReviewerState


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator):
    """A timezone-aware timestamp that survives SQLite.

    SQLite has no timestamp type: SQLAlchemy formats the datetime and silently drops
    the offset, so a 10:00+03:00 written before a restart reads back as a naive 10:00
    and gets treated as 10:00 UTC — every SLA in the database shifted by three hours.
    Normalising to UTC on the way in and re-attaching it on the way out removes the
    trap regardless of backend.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


#: Stored as its lowercase value ("pending", ...) rather than the member name, so the
#: column stays readable in `sqlite3` and survives a rename of the Python member.
REVIEWER_STATE = Enum(
    ReviewerState,
    name="reviewer_state",
    native_enum=False,
    length=32,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), default=utcnow, onupdate=utcnow
    )


class User(Base, TimestampMixin):
    """Anyone the bot has seen. `telegram_user_id` is required to send a DM at all."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    full_name: Mapped[str | None] = mapped_column(String(256))
    gitlab_username: Mapped[str | None] = mapped_column(String(128), index=True)
    #: One of i18n.SUPPORTED_LOCALES, or NULL to fall back to DEFAULT_LOCALE. Set from
    #: Telegram's language_code on first contact; overridable with /lang.
    locale: Mapped[str | None] = mapped_column(String(8))
    #: False once the bot learns it cannot DM this user (blocked / never started).
    can_be_dmed: Mapped[bool] = mapped_column(Boolean, default=True)
    muted_until: Mapped[datetime | None] = mapped_column(UtcDateTime())


class Review(Base, TimestampMixin):
    """One review post. Identified by the channel message it originated from."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("channel_chat_id", "channel_message_id", name="uq_review_channel_msg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    channel_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_message_id: Mapped[int] = mapped_column(Integer)

    #: The auto-forwarded copy in the linked discussion group, and the bot's card
    #: replying to it. Either may still be unknown if updates arrive out of order.
    discussion_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    discussion_message_id: Mapped[int | None] = mapped_column(Integer)
    card_message_id: Mapped[int | None] = mapped_column(Integer)

    #: Set once the "Автор:" handle (see `parsing.post_parser`) resolves to a numeric
    #: id — immediately if that person already talked to the bot, otherwise on their
    #: next /start (see `services.reviews.link_author_to_reviews`).
    author_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    author_label: Mapped[str | None] = mapped_column(String(256))
    #: The @handle itself, kept even before it resolves to an id — that's what the
    #: backfill on /start matches against.
    author_username: Mapped[str | None] = mapped_column(String(64), index=True)

    product: Mapped[str | None] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(String(512))
    task_url: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text, default="")

    posted_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    #: True once enough reviewers approved (see `services.reviews.approvals_needed`)
    #: or someone closes it by hand.
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    assignments: Mapped[list[ReviewerAssignment]] = relationship(
        back_populates="review", cascade="all, delete-orphan", lazy="selectin"
    )
    merge_requests: Mapped[list[MergeRequestLink]] = relationship(
        back_populates="review", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def approvals(self) -> int:
        return sum(
            1
            for item in self.assignments
            if item.state == ReviewerState.APPROVED and item.removed_at is None
        )


class MergeRequestLink(Base):
    """One MR referenced by a review. A post commonly carries several."""

    __tablename__ = "merge_request_links"
    __table_args__ = (
        UniqueConstraint("review_id", "project_path", "iid", name="uq_mr_per_review"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), index=True)

    host: Mapped[str] = mapped_column(String(256))
    project_path: Mapped[str] = mapped_column(String(512))
    iid: Mapped[int] = mapped_column(Integer)
    #: "gitlab" or "github" — which URL shape to reconstruct in `web_url`, and (for
    #: "github") the signal `services.gitlab_sync` uses to skip a link the GitLab API
    #: could never answer for. See `parsing.gitlab_url.MergeRequestRef`.
    platform: Mapped[str] = mapped_column(String(16), default="gitlab")

    #: Cheap MR-wide signal used when a reviewer has no GitLab mapping.
    blocking_discussions_resolved: Mapped[bool | None] = mapped_column(Boolean)
    last_synced_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    sync_error: Mapped[str | None] = mapped_column(Text)

    review: Mapped[Review] = relationship(back_populates="merge_requests")

    @property
    def web_url(self) -> str:
        if self.platform == "github":
            return f"https://{self.host}/{self.project_path}/pull/{self.iid}"
        return f"https://{self.host}/{self.project_path}/-/merge_requests/{self.iid}"


class ReviewerAssignment(Base, TimestampMixin):
    """The (review, reviewer) pair — the unit the state machine and escalation act on."""

    __tablename__ = "reviewer_assignments"
    __table_args__ = (
        UniqueConstraint("review_id", "mention_key", name="uq_assignment_per_review"),
        Index("ix_assignment_open", "state", "review_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), index=True)

    #: Stable identity from the post: "un:<username>" or "id:<telegram id>".
    mention_key: Mapped[str] = mapped_column(String(96))
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    display_label: Mapped[str] = mapped_column(String(128), default="")

    state: Mapped[ReviewerState] = mapped_column(
        REVIEWER_STATE, default=ReviewerState.PENDING, index=True
    )
    ball_since: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime())

    last_nudge_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    nudges_today: Mapped[int] = mapped_column(Integer, default=0)
    snoozed_until: Mapped[datetime | None] = mapped_column(UtcDateTime())

    #: Set once, so we don't spam the thread asking an unregistered reviewer to /start.
    registration_hint_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Set when a later edit of the post no longer names this reviewer. Never deleted
    #: outright — a verdict already given must survive the post being reworded — but a
    #: removed row stops counting toward quorum, drops off the card, and is not nudged.
    #: Cleared if the same handle is named again in a later edit.
    removed_at: Mapped[datetime | None] = mapped_column(UtcDateTime())

    review: Mapped[Review] = relationship(back_populates="assignments")


class AnnouncementDraft(Base, TimestampMixin):
    """A `/announce`-in-progress: composed in a DM, published (or cancelled) later.

    Server-side state rather than an in-memory dict — `callback_data` is capped at 64
    bytes so it can only carry this row's id, and this app already treats every other
    transient-but-restart-surviving thing (`snoozed_until`, `nudges_today`) as a DB
    row rather than process memory. JSON-in-Text for the list-shaped fields is new
    here: drafts are short-lived and never queried relationally, unlike
    `Review`/`MergeRequestLink`, so a child table would be overkill.
    """

    __tablename__ = "announcement_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)

    composer_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    composer_username: Mapped[str] = mapped_column(String(64))
    #: The composer's private chat — where the preview lives and gets edited in place.
    chat_id: Mapped[int] = mapped_column(BigInteger)
    preview_message_id: Mapped[int | None] = mapped_column(Integer)

    #: Always a real configured path, even for a draft that names no MR at all: the
    #: composer picks a product instead and `services.announcements` stores one
    #: project carrying it, so the config lookup on reroll stays a plain dict hit.
    project_path: Mapped[str] = mapped_column(String(512))
    product: Mapped[str] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(String(512))
    task_url: Mapped[str | None] = mapped_column(Text)
    docs_url: Mapped[str | None] = mapped_column(Text)
    #: The template's "Описание:" line — what a change is about when it has no docs
    #: page to link to (an SQL-only fix, say).
    description: Mapped[str | None] = mapped_column(Text)
    #: JSON list of {host, project_path, iid}.
    merge_requests_json: Mapped[str] = mapped_column(Text, default="[]")

    techlead_username: Mapped[str | None] = mapped_column(String(64))
    #: JSON list of usernames — the rerollable slot(s).
    pool_pick_usernames_json: Mapped[str] = mapped_column(Text, default="[]")

    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class AssignmentTransition(Base):
    """One state change on a (review, reviewer) pair, with its timestamp.

    `ReviewerAssignment` itself only ever holds the *current* state: `ball_since` and
    `decided_at` are overwritten on every transition (see `services.reviews.apply_verdict`),
    so the instant a reviewer's ✍️ becomes 👍, there is no longer any row anywhere
    recording how long the ✍️ stood. This table is the append-only history that
    survives it — the only source `services.stats` has for per-person durations:
    author fix time (a row entering `changes_requested` paired with that same
    assignment's next row leaving it) and reviewer response time (an assignment's very
    first row). Necessarily forward-looking only: nothing before this table existed
    can be reconstructed.
    """

    __tablename__ = "assignment_transitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("reviewer_assignments.id", ondelete="CASCADE"), index=True
    )
    #: Denormalized from the assignment for stats queries that aggregate by author
    #: (a `Review`-level fact) without an extra join on every row.
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), index=True)

    from_state: Mapped[ReviewerState] = mapped_column(REVIEWER_STATE)
    to_state: Mapped[ReviewerState] = mapped_column(REVIEWER_STATE)
    event: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)

    assignment: Mapped[ReviewerAssignment] = relationship(lazy="selectin")
    review: Mapped[Review] = relationship(lazy="selectin")


class NudgeLog(Base):
    """Audit trail of every DM sent — makes "why did it ping me" answerable."""

    __tablename__ = "nudge_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("reviewer_assignments.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(String(64))
    sent_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, index=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
