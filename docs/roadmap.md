# Roadmap

## Next: ES-RU

Spanish for a Russian speaker at A1–A2. The German modes serve B1–B2, so this
mode has to teach the base.

- The mode is `ES-RU`: the studied language comes first. It needs one
  `Deck.LanguageMode` entry, `el` and `la` added to `Card.Article` in
  `cards/models.py`, one choices-only migration for both, and a folder
  `languages/es/` with `pack.yml` and drill files (see
  [language_packs.md](language_packs.md)).
- A pack without a video channel (`media.video_channel: null`) has to work:
  the dashboard and the daily plan cannot assume a channel today.
- Drills are data: gender, conjugation, `ser` / `estar` and similar are YAML
  files with `typed`, `choice` and `pool_choice` fields. No Python, template,
  URL or migration per drill.
- Word capture starts as lemma plus translation. Merging the German and Dutch
  capture pipelines comes later.
- Content is the author's own or openly licensed.

## Later ideas

- Better cloze-deletion cards.
- Optional daily review limits.
- A better scheduler (FSRS-like).
- More browse filters and bulk card actions.
- Better form styling and validation messages.
- AI-assisted card generation from pasted text.
- Deck sharing between users.
- Email-based password reset.
- Media and audio on cards.
- Anki import and export.
- Dark mode.
- Off-site backups: built, off until a destination is configured
  ([backups.md](backups.md)).
