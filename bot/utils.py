from asgiref.sync import sync_to_async


async def run_db(func, *args, **kwargs):
    """Runs a synchronous Django-ORM function off the event loop and awaits its result.
    thread_sensitive keeps all DB access on one worker thread, matching SQLite's
    single-writer model and avoiding cross-thread connection issues."""
    return await sync_to_async(func, thread_sensitive=True)(*args, **kwargs)
