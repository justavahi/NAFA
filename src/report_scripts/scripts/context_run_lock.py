"""Prevent two CLI runners from submitting the same study jobs concurrently."""
from contextlib import contextmanager
import fcntl


@contextmanager
def study_lock(out):
    with (out / "runner.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("study already running; wait before scoring/resuming to avoid duplicate API calls") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
