"""Previously proposed Wilson score: an explicit experiment, not a selected policy."""

import math


def wilson_preview(successes, samples):
    """Return a 0..100 lower-bound ranking score, or None without evidence."""
    if type(successes) is not int or type(samples) is not int or not 0 <= successes <= samples:
        raise ValueError("Invalid observation counts")
    if samples == 0:
        return None
    z = 1.96
    p = successes / samples
    lower = (p + z*z/(2*samples) - z*math.sqrt(p*(1-p)/samples + z*z/(4*samples*samples)))
    return max(0.0, min(100.0, 100 * lower / (1 + z*z/samples)))
