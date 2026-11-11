from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from cards.articles import GERMAN_ARTICLES, parse_leading_article
from core.language_packs import GERMAN, pack_for_mode
from decks.models import Deck

LEECH_THRESHOLD = 8
GLOBAL_DECK_RETIRE_STREAK = 3


class CardQuerySet(models.QuerySet):
    def active(self):
        return self.filter(
            is_suspended=False,
            is_retired=False,
            deck__is_archived=False,
        )

    def for_user(self, user):
        return self.filter(deck__owner=user)

    def due(self):
        return self.active().filter(due_at__lte=timezone.now()).order_by("due_at", "created_at")

    def search(self, text):
        if not text:
            return self
        return self.filter(
            Q(front__icontains=text)
            | Q(back__icontains=text)
            | Q(example_sentence__icontains=text)
            | Q(notes__icontains=text)
            | Q(tags__icontains=text)
        )

    def nouns(self):
        return self.exclude(article="")

    def leeches(self):
        return self.filter(lapse_count__gte=LEECH_THRESHOLD)


class Card(models.Model):
    class CardType(models.TextChoices):
        VOCABULARY = "vocabulary", "Vocabulary"
        TRANSLATION = "translation", "Translation"
        GRAMMAR = "grammar", "Grammar"
        CLOZE = "cloze", "Cloze"
        CUSTOM = "custom", "Custom"

    class Article(models.TextChoices):
        DER = "der", "der"
        DIE = "die", "die"
        DAS = "das", "das"
        DE = "de", "de"
        HET = "het", "het"

    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="cards")
    front = models.TextField()
    back = models.TextField()
    example_sentence = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    tags = models.CharField(max_length=255, blank=True)
    article = models.CharField(max_length=3, choices=Article.choices, blank=True, default="")
    plural = models.CharField(max_length=120, blank=True, default="")
    card_type = models.CharField(
        max_length=32,
        choices=CardType.choices,
        default=CardType.VOCABULARY,
    )
    due_at = models.DateTimeField(default=timezone.now)
    interval_days = models.PositiveIntegerField(default=0)
    ease_factor = models.FloatField(default=2.5)
    review_count = models.PositiveIntegerField(default=0)
    lapse_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_suspended = models.BooleanField(default=False)
    correct_streak = models.PositiveIntegerField(default=0, db_index=True)
    is_retired = models.BooleanField(default=False, db_index=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    source_url = models.URLField(blank=True)

    objects = CardQuerySet.as_manager()

    class Meta:
        ordering = ["due_at", "created_at"]
        indexes = [
            models.Index(fields=["due_at"]),
            models.Index(fields=["card_type"]),
            models.Index(fields=["is_suspended"]),
            models.Index(fields=["is_retired"]),
        ]

    def __str__(self):
        return self.front[:80]

    def get_absolute_url(self):
        return reverse("cards:detail", kwargs={"pk": self.pk})

    @property
    def can_retire(self):
        return (
            self.deck.is_global
            and not self.is_retired
            and self.correct_streak >= GLOBAL_DECK_RETIRE_STREAK
        )

    def retire(self):
        if not self.can_retire:
            return False
        self.is_retired = True
        self.retired_at = timezone.now()
        self.save(update_fields=["is_retired", "retired_at", "updated_at"])
        return True

    @property
    def noun_stem(self):
        # A card built in memory for answer-checking (reviews.study) may have
        # no deck attached; fall back to the German articles.
        articles = (
            pack_for_mode(self.deck.language_mode).articles if self.deck_id else GERMAN_ARTICLES
        )
        return parse_leading_article(self.front, articles)[1]

    @property
    def article_options(self):
        pack = pack_for_mode(self.deck.language_mode)
        return [
            {"value": value, "gender": gender, "key": index}
            for index, (value, gender) in enumerate(
                zip(pack.articles, pack.article_genders, strict=True), start=1
            )
        ]

    @property
    def speech_locale(self):
        # A card built in memory for answer-checking (reviews.study) may have
        # no deck attached; fall back to the German locale.
        pack = pack_for_mode(self.deck.language_mode) if self.deck_id else GERMAN
        return pack.speech_locale

    @property
    def display_front(self):
        if self.article and not self.front.casefold().startswith(f"{self.article} "):
            return f"{self.article} {self.front}"
        return self.front
