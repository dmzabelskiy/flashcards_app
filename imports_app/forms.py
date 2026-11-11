from django import forms

from core.language_modes import filter_decks_for_mode
from decks.models import Deck


class CsvImportForm(forms.Form):
    deck = forms.ModelChoiceField(queryset=Deck.objects.none())
    csv_file = forms.FileField()

    def __init__(self, *args, language_mode=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Deck.objects.filter(is_archived=False)
        if user is not None:
            queryset = queryset.filter(owner=user)
        if language_mode:
            queryset = filter_decks_for_mode(queryset, language_mode)
        self.fields["deck"].queryset = queryset
