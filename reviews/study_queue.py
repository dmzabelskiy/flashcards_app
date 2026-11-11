import random

STUDY_SESSION_LIMIT = 20


def select_study_queue(cards, limit=STUDY_SESSION_LIMIT):
    """Pick card ids from an already-scoped active queryset.

    Due cards come first, oldest due date first. A short queue is topped up
    with the cards that fall due soonest. Only the selected pool is shuffled,
    so which cards are studied is decided by priority, not by chance.
    Pass ``None`` to study the entire pool, including already reviewed cards.
    """
    if limit is not None and limit <= 0:
        return []
    # One read keeps the pool coherent if a concurrent review changes due_at.
    # Ascending due_at already puts every due card before every future card.
    pool = cards.order_by("due_at", "created_at", "id").values_list("id", flat=True)
    if limit is not None:
        pool = pool[:limit]
    queue = list(pool)
    random.shuffle(queue)
    return queue
