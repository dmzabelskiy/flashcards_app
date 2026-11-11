import logging

from video_study.metadata import LemmaCacheUnavailable, get_lemmatizer

logger = logging.getLogger(__name__)

_lemma_cache_unavailable_logged = False


def lemmatize_spanish(word):
    """Return ``(lemma, available)`` for a single Spanish token, offline.

    ``available`` is false only when the lemma cache could not be read, in
    which case ``lemma`` is the token unchanged (lower-cased).
    """
    global _lemma_cache_unavailable_logged
    lowered = word.lower()
    try:
        return get_lemmatizer().lemmatize(lowered, lang="es"), True
    except LemmaCacheUnavailable:
        if not _lemma_cache_unavailable_logged:
            logger.warning(
                "Lemma cache unavailable; Spanish word capture will return tokens "
                "unlemmatized until the cache is rebuilt."
            )
            _lemma_cache_unavailable_logged = True
        return lowered, False
