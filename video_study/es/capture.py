from video_study.es.lemma import lemmatize_spanish
from video_study.normalization import CaptureResolution

ES_ARTICLE_TO_GENDER = {"el": "masculine", "la": "feminine"}


def resolve_capture_es(text, context, budget_start=None):
    """Return the card fields for a Spanish word selected in a transcript.

    There is no Spanish dictionary behind this: the lemma comes from the offline
    lemmatiser, and the article, plural and part of speech stay empty (the owner
    sets an article on the card). ``context`` and ``budget_start`` are accepted for
    the resolver contract; nothing is sent anywhere from here.
    """
    text = " ".join(str(text or "").split()).strip()
    if " " in text:
        return CaptureResolution(
            lemma=text,
            article="",
            plural="",
            part_of_speech="phrase",
            example_sentence="",
            dictionary_status="skipped",
            trustworthy=True,
        )

    lemma, available = lemmatize_spanish(text)
    return CaptureResolution(
        lemma=lemma,
        article="",
        plural="",
        part_of_speech="",
        example_sentence="",
        # No dictionary is consulted, so the panel must not say one is unavailable.
        dictionary_status="skipped",
        trustworthy=available,
    )
