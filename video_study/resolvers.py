"""Which capture resolver and gender map serve a transcript's source language."""

from video_study.es.capture import ES_ARTICLE_TO_GENDER, resolve_capture_es
from video_study.metadata import ARTICLE_TO_GENDER
from video_study.nl.capture import NL_ARTICLE_TO_GENDER, resolve_capture_nl
from video_study.normalization import resolve_capture


def capture_for_language(source_language):
    """Return ``(resolver, article_to_gender)`` for a source language.

    German and English captions both go through the German resolver, as they
    always have. Names are read at call time, so tests can patch them here.
    """
    if source_language == "nl":
        return resolve_capture_nl, NL_ARTICLE_TO_GENDER
    if source_language == "es":
        return resolve_capture_es, ES_ARTICLE_TO_GENDER
    return resolve_capture, ARTICLE_TO_GENDER
