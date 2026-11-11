from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse


class DeckQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(owner=user)


class Deck(models.Model):
    class LanguageMode(models.TextChoices):
        DE_RU = "DE-RU", "Russian"
        DE_ES = "DE-ES", "Spanish"
        NL_EN = "NL-EN", "English"
        ES_RU = "ES-RU", "Russian"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="decks",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    language_mode = models.CharField(
        max_length=8,
        choices=LanguageMode.choices,
        default=LanguageMode.DE_RU,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    is_global = models.BooleanField(
        default=False,
        db_index=True,
        help_text="The main vocabulary deck for this language mode.",
    )

    objects = DeckQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "language_mode"],
                condition=Q(is_global=True),
                name="unique_global_deck_per_owner_mode",
            )
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("decks:detail", kwargs={"pk": self.pk})
