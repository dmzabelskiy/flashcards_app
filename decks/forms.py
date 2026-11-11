from django import forms

from decks.models import Deck


class DeckForm(forms.ModelForm):
    class Meta:
        model = Deck
        fields = ["name", "description", "is_archived"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }
