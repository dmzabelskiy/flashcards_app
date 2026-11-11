from django.conf import settings
from django.db import models

from decks.models import Deck


class DailyTaskCompletion(models.Model):
    """One explicit "I finished this" mark for a daily-plan task.

    Opening a lesson or a video is analytics (``TrainerAttempt``); only a row
    here lets Today's checklist count media as done. One row per
    owner, language mode, task and local calendar day.
    """

    class Task(models.TextChoices):
        # Retired task; kept so stored history still loads.
        GRAMMAR = "grammar", "Grammar"
        MEDIA = "media", "Video or podcast"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daily_task_completions",
    )
    language_mode = models.CharField(max_length=8, choices=Deck.LanguageMode.choices)
    task = models.CharField(max_length=16, choices=Task.choices)
    date = models.DateField()
    item_label = models.CharField(max_length=240, blank=True)
    completed_at = models.DateTimeField()

    class Meta:
        ordering = ["-date", "-completed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "language_mode", "task", "date"],
                name="unique_daily_task_completion",
            )
        ]

    def __str__(self):
        return f"{self.owner} · {self.language_mode} · {self.task} · {self.date}"
