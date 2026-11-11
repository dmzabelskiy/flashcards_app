# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Existing: Django 5.2 templates, plain CSS in `static/css/`, small vanilla
JavaScript files, no build step, no frontend dependencies. Served by
gunicorn and WhiteNoise on Fly.io.

## Users and scene

- Primary user: a learner at roughly B1–B2 level in German and A1–A2 in
  Spanish, plus a few invited learners whose accounts the owner creates in
  Django admin. No public signup.
- Scene: short daily study sessions, mostly on a laptop; the phone must work
  but is secondary.
- Job: keep a daily habit going: study 20 words, run one drill, watch or
  listen to something captioned. Study is German → Russian or German →
  Spanish; smaller Dutch → English and Spanish → Russian modes have cards, the
  article drill and video (Spanish adds three trainers). The mode is chosen per session and stored on each deck
  (`core/language_modes.py`).

## What the product does

- Decks and cards owned per account, imported from CSV word lists, browsed
  and edited in a library.
- Spaced review of due cards with a simple scheduler; typed study with typo
  tolerance and a daily goal of 20 correct words.
- Drills on one shared engine: der/die/das articles (el/la in Spanish),
  adjective + preposition + case, verb + preposition, irregular verbs, Spanish
  verb forms, verbs in context and small words, and a flashcard memory mode.
- Watch & listen: YouTube and podcast transcripts with click-to-translate and
  a global deck of captured words, one per account and language mode.
- Dashboard "Today" with the daily plan, streak, weekly stats and activity.

## Mechanism and position

A private, keyboard-fast study instrument for a handful of people, not a
marketplace of shared sets. The differentiator is the daily plan that ties
vocabulary, a drill and media into one loop, and drills specific to German
(articles, cases, prepositions).

## Durable constraints

- Every page requires login except the login screen and `/healthz`, which
  returns no user data.
- Plain CSS and JavaScript only, no build step, no external frontend
  dependencies. The only font is the self-hosted Archivo.
- Per-user data stays private; shared caches (transcripts, translations,
  catalogs) are global.
- Accessibility floor already in the codebase: 44 px targets, visible
  focus, `aria-live` feedback, never colour alone for state, semantic
  fieldsets in drills, reduced-motion respected.
- Article colours (der / die / das; `el` and `la` take the colours of `der`
  and `die`) are a learning device and must stay
  distinguishable from one another and from success/error state.

## Brand commitments

- A white, blue and light-blue palette. Palette only; layout and component
  feel are open.
- Must read as its own product.
- White dominates; blue is the accent, never the ground of whole pages.
- Name: "German Flashcards". Product mark is a single letterform "G".

## Terminology

Today (dashboard), Study words, Drills, Watch & listen, Cards, Decks, Import,
Stats, global deck, retire (a word).
