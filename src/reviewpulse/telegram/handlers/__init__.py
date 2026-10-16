from aiogram import Router

from . import admin, announce, callbacks, commands, myposts, posts


def build_router() -> Router:
    router = Router(name="root")
    # Commands first: they are the narrowest filters and must not be shadowed.
    router.include_router(commands.router)
    router.include_router(admin.router)
    router.include_router(announce.router)
    router.include_router(myposts.router)
    router.include_router(callbacks.router)
    router.include_router(posts.router)
    return router


__all__ = ["build_router"]
