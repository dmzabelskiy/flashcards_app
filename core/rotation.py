"""Fair, deterministic daily rotation.

Picking a "thing of the day" (drill, video, podcast) with ``hash(date) % n``
hashes each day independently, which is a *sampling*, not a rotation: it
repeats the same item on nearby days and clusters (a drill could land three
days running, a video every other day), which reads as "stale".

Instead we fix one permutation of ``range(n)`` per ``salt`` and walk it by the
day's ordinal. Consecutive days step to adjacent positions of a permutation, so
they can never collide, and every window of ``n`` consecutive days visits each
item exactly once. Distinct salts get independent permutations so unrelated
rotations (drill vs. video) don't march in lockstep.
"""

import hashlib


def _permutation(n, seed_key):
    """A deterministic shuffle of ``range(n)`` derived from ``seed_key``."""
    order = list(range(n))
    for i in range(n - 1, 0, -1):
        digest = hashlib.sha256(f"{seed_key}:{i}".encode()).digest()
        j = int.from_bytes(digest[:8], byteorder="big") % (i + 1)
        order[i], order[j] = order[j], order[i]
    return order


def rotating_index(n, day, salt=""):
    """Index in ``range(n)`` for ``day``; a fair rotation, not a resample."""
    if n <= 0:
        raise ValueError("n must be a positive integer")
    if n == 1:
        return 0
    permutation = _permutation(n, salt)
    return permutation[day.toordinal() % n]


def rotating_choice(items, day, salt=""):
    """Pick one of ``items`` for ``day``; ``None`` when ``items`` is empty."""
    items = list(items)
    if not items:
        return None
    return items[rotating_index(len(items), day, salt)]
