from aiogram import Router

from . import callbacks, commands, posts


def build_router() -> Router:
    router = Router(name="root")
    # Commands first: they are the narrowest filters and must not be shadowed.
    router.include_router(commands.router)
    router.include_router(callbacks.router)
    router.include_router(posts.router)
    return router


__all__ = ["build_router"]
