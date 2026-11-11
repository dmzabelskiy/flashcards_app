from django import forms

from cards.models import Card
from core.language_modes import filter_decks_for_mode
from core.language_packs import pack_for_mode
from decks.models import Deck


class CardForm(forms.ModelForm):
    class Meta:
        model = Card
        fields = [
            "deck",
            "front",
            "back",
            "example_sentence",
            "notes",
            "tags",
            "article",
            "plural",
            "card_type",
            "due_at",
            "is_suspended",
        ]
        widgets = {
            "front": forms.Textarea(attrs={"rows": 3}),
            "back": forms.Textarea(attrs={"rows": 3}),
            "example_sentence": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "due_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def __init__(self, *args, language_mode=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["due_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        queryset = Deck.objects.filter(is_archived=False)
        if user is not None:
            queryset = queryset.filter(owner=user)
        if language_mode:
            queryset = filter_decks_for_mode(queryset, language_mode)
            self.fields["article"].choices = [("", "---------")] + [
                (article, article) for article in pack_for_mode(language_mode).articles
            ]
        self.fields["deck"].queryset = queryset

    def clean(self):
        cleaned_data = super().clean()
        deck = cleaned_data.get("deck")
        article = cleaned_data.get("article")
        if deck is not None and article:
            if article not in pack_for_mode(deck.language_mode).articles:
                self.add_error("article", "This article isn't used in the deck's language.")
        return cleaned_data


class CardSearchForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    deck = forms.IntegerField(required=False)
    tag = forms.CharField(required=False)
    card_type = forms.ChoiceField(
        required=False, choices=[("", "All types")] + list(Card.CardType.choices)
    )
    due_only = forms.BooleanField(required=False)
    suspended = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All cards"),
            ("active", "Not suspended"),
            ("suspended", "Suspended"),
        ],
    )
    leech = forms.BooleanField(required=False)
