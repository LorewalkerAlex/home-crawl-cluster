"""Confirmed finite-history policy; all feedback uses the same functions."""

import math


def wilson(successes, samples, z=1.96):
    if not samples:
        return None
    p = successes / samples
    lower = (
        p
        + z * z / (2 * samples)
        - z * math.sqrt(p * (1 - p) / samples + z * z / (4 * samples * samples))
    )
    return max(0.0, min(100.0, 100 * lower / (1 + z * z / samples)))


def needs_test(feedback_at, scheduled_at, now_ms, minimum_ms, timeout_ms):
    due = feedback_at is None or now_ms - feedback_at >= minimum_ms
    ready = (
        scheduled_at is None
        or (feedback_at is not None and feedback_at >= scheduled_at)
        or now_ms - scheduled_at >= timeout_ms
    )
    return due and ready


def proxy_url(scheme, host, port):
    return "{}://{}:{}".format(scheme, "[" + host + "]" if ":" in host else host, port)
