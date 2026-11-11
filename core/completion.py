"""Explicit daily completion of the media task.

Integration contract used by ``reviews.daily_plan``: both helpers take the
literal task names ``"media"`` and ``"grammar"`` and are scoped to the owner,
the active language mode, and a local calendar day. The ``"grammar"`` task
name is a legacy value: only stored history uses it, and nothing writes it.
"""

from django.utils import timezone

from core.models import DailyTaskCompletion
from decks.models import Deck

TASKS = frozenset(DailyTaskCompletion.Task.values)
ITEM_LABEL_MAX_LENGTH = DailyTaskCompletion._meta.get_field("item_label").max_length


def _validated(task, language_mode):
    if task not in TASKS:
        raise ValueError(f"Unsupported daily task: {task!r}")
    if language_mode not in Deck.LanguageMode.values:
        raise ValueError(f"Unsupported language mode: {language_mode!r}")


def task_is_complete(user, task, language_mode, date=None):
    """Whether ``user`` marked ``task`` complete in ``language_mode`` on ``date``."""
    _validated(task, language_mode)
    return DailyTaskCompletion.objects.filter(
        owner=user,
        task=task,
        language_mode=language_mode,
        date=date or timezone.localdate(),
    ).exists()


def mark_task_complete(user, task, language_mode, item_label="", date=None):
    """Record the completion once per day; repeat calls keep the first mark.

    Returns ``(completion, created)``.
    """
    _validated(task, language_mode)
    completion, created = DailyTaskCompletion.objects.get_or_create(
        owner=user,
        task=task,
        language_mode=language_mode,
        date=date or timezone.localdate(),
        defaults={
            "item_label": str(item_label or "")[:ITEM_LABEL_MAX_LENGTH],
            "completed_at": timezone.now(),
        },
    )
    return completion, created
