GERMAN_ARTICLES = ("der", "die", "das")
ARTICLES = set(GERMAN_ARTICLES)


def parse_leading_article(text, articles=GERMAN_ARTICLES):
    """Return (article, stem) from a noun string like "der Tisch" or "het huis"."""
    stripped = (text or "").strip()
    parts = stripped.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() in articles:
        return parts[0].lower(), parts[1].strip()
    return "", stripped
