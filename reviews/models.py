from django.db import models

from cards.models import Card
from decks.models import Deck
from reviews.ratings import Rating


class ReviewLogQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(card__deck__owner=user)


class ReviewLog(models.Model):
    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="review_logs")
    rating = models.CharField(max_length=16, choices=Rating.choices)
    reviewed_at = models.DateTimeField()
    previous_due_at = models.DateTimeField()
    new_due_at = models.DateTimeField()
    previous_interval_days = models.PositiveIntegerField()
    new_interval_days = models.PositiveIntegerField()
    elapsed_seconds = models.PositiveIntegerField(null=True, blank=True)
    # Echo of the rendered form's one-time token; the unique constraint is the
    # last line of defence against a duplicate submission advancing a card twice.
    submission_id = models.CharField(
        max_length=80, null=True, blank=True, unique=True, editable=False
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ReviewLogQuerySet.as_manager()

    class Meta:
        ordering = ["-reviewed_at", "-created_at"]
        indexes = [
            models.Index(fields=["reviewed_at"]),
            models.Index(fields=["rating"]),
        ]

    def __str__(self):
        return f"{self.card_id} {self.rating} at {self.reviewed_at:%Y-%m-%d %H:%M}"


class DailyStudyProgressQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(deck__owner=user)


class DailyStudyProgress(models.Model):
    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="daily_progress")
    date = models.DateField()
    correct_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DailyStudyProgressQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["deck", "date"], name="unique_daily_study_progress_per_deck_date"
            ),
        ]
        indexes = [
            models.Index(fields=["date"]),
        ]

    def __str__(self):
        return f"{self.deck} {self.date}: {self.correct_count}"


class TrainerAttempt(models.Model):
    class Trainer(models.TextChoices):
        TYPED_REVIEW = "typed_review", "Typed review"
        SPACED_REVIEW = "spaced_review", "Quick review"
        ARTICLE = "article", "Article trainer"
        ADJECTIVE_PREPOSITION = "adjective_preposition", "Adjective + preposition"
        VERB_PREPOSITION = "verb_preposition", "Verb + preposition"
        IRREGULAR_VERB = "irregular_verb", "Irregular verbs"
        # Retired trainer; kept so stored history still loads.
        DEUTSCH_ONLINE = "deutsch_online", "Deutsch-online"
        VIDEO = "video", "Video study"
        PODCAST = "podcast", "Podcast study"
        # Retired trainer; kept so stored history still loads.
        GRAMMAR = "grammar", "Grammar book"
        # Retired trainer; kept so stored history still loads.
        TELC_B2 = "telc_b2", "telc Deutsch B2"

    owner = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="trainer_attempts",
    )
    # Any drill's trainer name; the drill files, not this model, list them.
    trainer = models.CharField(max_length=32, db_index=True)
    language_mode = models.CharField(
        max_length=8,
        choices=Deck.LanguageMode.choices,
        default=Deck.LanguageMode.DE_RU,
        db_index=True,
    )
    card = models.ForeignKey(
        Card,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trainer_attempts",
    )
    item_label = models.CharField(max_length=240, blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    response_ms = models.PositiveIntegerField(null=True, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-created_at"]
        indexes = [
            models.Index(fields=["owner", "occurred_at"]),
            models.Index(fields=["trainer", "occurred_at"]),
            models.Index(fields=["language_mode", "occurred_at"]),
        ]

    @property
    def trainer_label(self):
        """The drill's label, else a legacy trainer's label, else the stored name."""
        # Imported here: the drill registry reads files, and models load first.
        from reviews.drills import REGISTRY

        drill = REGISTRY.by_trainer(self.trainer)
        if drill is not None:
            return drill.label
        try:
            return self.Trainer(self.trainer).label
        except ValueError:
            return self.trainer

    def __str__(self):
        return f"{self.trainer_label} · {self.item_label or 'session'}"


class DrillItemProgress(models.Model):
    """One user's standing with one item of one drill.

    ``item_key`` is the drill's own stable key: the card pk for the article
    drill and the entry id for the reference drills.  Three correct answers in
    a row offer retirement, and a retired item leaves the drill's pool and its
    flashcard set.
    """

    owner = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="drill_item_progress",
    )
    trainer = models.CharField(max_length=32, db_index=True)
    item_key = models.CharField(max_length=200)
    correct_streak = models.PositiveIntegerField(default=0)
    retired_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "trainer", "item_key"],
                name="unique_drill_item_progress_per_item",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "trainer", "retired_at"]),
        ]

    def __str__(self):
        state = "retired" if self.retired_at else f"streak {self.correct_streak}"
        return f"{self.owner_id} · {self.trainer} · {self.item_key} · {state}"
