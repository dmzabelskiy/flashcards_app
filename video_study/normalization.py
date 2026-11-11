from dataclasses import dataclass

from video_study.dictionary import FOUND, lookup_dictionary
from video_study.metadata import _parse_inflected_article, suggest_word_metadata


@dataclass(frozen=True)
class CaptureResolution:
    lemma: str
    article: str
    plural: str
    part_of_speech: str
    example_sentence: str
    dictionary_status: str
    trustworthy: bool
    translation: str = ""


def resolve_capture(text, context, budget_start=None):
    """Return the finished card fields for a word selected in a transcript.

    ``text`` must be the selection as the owner clicked it, not the
    casefolded cache key: casefolding turns "ß" into "ss". ``context`` is
    used in memory to choose the lemma and is never sent anywhere.
    """
    text = " ".join(str(text or "").split()).strip()
    context = " ".join(str(context or "").split()).strip()
    suggestion = suggest_word_metadata(text, context, source_form=text)
    if suggestion["part_of_speech"] == "phrase":
        return CaptureResolution(
            lemma=suggestion["normalized_word"],
            article="",
            plural="",
            part_of_speech="phrase",
            example_sentence="",
            dictionary_status="skipped",
            trustworthy=True,
        )

    _article, stem, has_article = _parse_inflected_article(text)
    surface_form = stem if has_article else text
    result = lookup_dictionary(
        suggestion["normalized_word"], surface_form, budget_start=budget_start
    )
    if result.status == FOUND:
        is_noun = result.part_of_speech == "noun"
        return CaptureResolution(
            lemma=result.lemma,
            article=result.article if is_noun else "",
            plural=result.plural if is_noun else "",
            part_of_speech=result.part_of_speech,
            example_sentence=result.example_sentence,
            dictionary_status=FOUND,
            trustworthy=True,
        )
    return CaptureResolution(
        lemma=suggestion["normalized_word"],
        article=suggestion["article"],
        plural="",
        part_of_speech=suggestion["part_of_speech"],
        example_sentence="",
        dictionary_status=result.status,
        trustworthy=suggestion["lemmatizer_available"],
    )
