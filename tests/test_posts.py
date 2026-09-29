"""`handlers.posts._post_locks`: the fix for a real production bug where the
channel-post and discussion-copy updates for the same post, dispatched concurrently,
both saw no card yet and both sent one — see the module docstring in `posts.py` for
the full story. No aiogram dispatch harness in this repo (see `test_announce_flow.py`),
so this tests the lock's mutual-exclusion semantics directly rather than trying to
reproduce the original race through real (and non-deterministic) DB/network timing.
"""

from __future__ import annotations

import asyncio

from reviewpulse.telegram.handlers import posts


async def test_the_same_post_key_always_maps_to_the_same_lock() -> None:
    """Both handlers derive the key from `(channel_chat_id, channel_message_id)` —
    if either ever computed it differently, they'd lock independently and the fix
    would be a no-op."""
    key = (-1001234567890, 42)
    assert posts._post_locks[key] is posts._post_locks[key]


async def test_a_second_acquire_of_the_same_key_waits_for_the_first_to_finish() -> None:
    key = (-999, 7)
    lock = posts._post_locks[key]
    order: list[str] = []
    first_has_the_lock = asyncio.Event()

    async def first() -> None:
        async with lock:
            order.append("first-start")
            first_has_the_lock.set()
            await asyncio.sleep(0)  # yield control, the way an awaited DB call would
            order.append("first-end")

    async def second() -> None:
        await first_has_the_lock.wait()
        async with lock:
            order.append("second-start")

    await asyncio.gather(first(), second())

    assert order == ["first-start", "first-end", "second-start"]


async def test_different_posts_do_not_block_each_other() -> None:
    lock_a = posts._post_locks[(-1, 1)]
    lock_b = posts._post_locks[(-1, 2)]
    order: list[str] = []
    a_has_the_lock = asyncio.Event()

    async def holds_a() -> None:
        async with lock_a:
            a_has_the_lock.set()
            order.append("a-start")
            await asyncio.sleep(0.01)
            order.append("a-end")

    async def wants_b() -> None:
        await a_has_the_lock.wait()
        async with lock_b:
            order.append("b-start")

    await asyncio.gather(holds_a(), wants_b())

    # b does not wait for a to release — it interleaves rather than queuing behind it.
    assert order.index("b-start") < order.index("a-end")
