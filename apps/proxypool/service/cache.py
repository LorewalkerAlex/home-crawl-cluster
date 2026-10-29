"""Small per-target candidate queues; TTL does not slide on each acquisition."""

import asyncio
from collections import deque
import random
import time


class CandidateCache:
    def __init__(self, settings):
        self.settings = settings
        self.queues = {}
        self.locks = {}

    def clear(self, target_id=None):
        if target_id is None:
            self.queues.clear()
        else:
            self.queues.pop(target_id, None)

    async def acquire(self, target, load):
        identifier = target["target_id"]
        if not target["enabled"]:
            return None
        lock = self.locks.setdefault(identifier, asyncio.Lock())
        async with lock:
            state = self.queues.get(identifier)
            now = time.monotonic()
            if (
                state is None
                or not state[2]
                or state[0] != target["revision"]
                or now - state[1] >= self.settings.candidate_ttl_seconds
            ):
                rows = await load(
                    identifier, self.settings.candidate_count, self.settings.candidate_min_score
                )
                random.SystemRandom().shuffle(rows)
                state = (target["revision"], time.monotonic(), deque(rows))
                self.queues[identifier] = state
            if not state[2]:
                return None
            return dict(
                state[2].popleft(), target_id=identifier, target_revision=target["revision"]
            )

    def forget_target(self, target_id):
        self.clear(target_id)
        self.locks.pop(target_id, None)
