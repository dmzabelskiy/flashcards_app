from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from decks.models import Deck
from decks.services import get_global_deck
from reviews.article_cards import seed_article_cards


class Command(BaseCommand):
    help = "Seed the 100 balanced B1–B2 German noun cards into a user's Russian deck."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="User who owns the global deck.")

    def handle(self, *args, **options):
        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=options["username"])
        except user_model.DoesNotExist as exc:
            raise CommandError(f"Unknown user: {options['username']}") from exc

        deck = get_global_deck(user, Deck.LanguageMode.DE_RU)
        count = seed_article_cards(deck)
        self.stdout.write(self.style.SUCCESS(f"Article deck ready with {count} card(s)."))
