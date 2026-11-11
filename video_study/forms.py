from django import forms

from video_study.services import InvalidYouTubeUrl, extract_youtube_id


class VideoUrlForm(forms.Form):
    SOURCE_LANGUAGE_CHOICES = (
        ("de", "German"),
        ("en", "English"),
        ("nl", "Dutch"),
    )

    url = forms.CharField(
        label="YouTube video URL",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "url",
                "inputmode": "url",
                "placeholder": "https://www.youtube.com/watch?v=…",
            }
        ),
    )
    source_language = forms.ChoiceField(
        label="Transcript language",
        choices=SOURCE_LANGUAGE_CHOICES,
        initial="de",
        required=False,
    )
    bilingual = forms.BooleanField(label="Dutch + English captions", required=False)

    def __init__(
        self,
        *args,
        allowed_source_languages=None,
        default_source_language="de",
        allow_bilingual=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.default_source_language = default_source_language
        if not allow_bilingual:
            del self.fields["bilingual"]
        if allowed_source_languages is not None:
            self.fields["source_language"].choices = [
                choice
                for choice in self.SOURCE_LANGUAGE_CHOICES
                if choice[0] in allowed_source_languages
            ]

    def clean_url(self):
        value = self.cleaned_data["url"].strip()
        try:
            self.youtube_id = extract_youtube_id(value)
        except InvalidYouTubeUrl as exc:
            raise forms.ValidationError(str(exc)) from exc
        return value

    def clean_source_language(self):
        return self.cleaned_data.get("source_language") or self.default_source_language
