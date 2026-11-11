import json
import time

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_POST

from core.completion import task_is_complete
from core.language_modes import (
    get_language_mode,
    get_source_language,
    get_target_language,
    get_target_language_label,
)
from decks.models import Deck
from reviews.analytics import record_trainer_session
from reviews.models import TrainerAttempt
from video_study.forms import VideoUrlForm
from video_study.models import PodcastEpisode, TranscriptVideo, WordTranslation
from video_study.resolvers import capture_for_language
from video_study.services import (
    PodcastUnavailable,
    TranscriptUnavailable,
    TranslationUnavailable,
    canonical_youtube_url,
    clean_translation_text,
    fetch_english_captions,
    fetch_transcript,
    implicit_catalog_refresh_enabled,
    normalize_selection,
    refresh_podcast_catalog,
    request_translation,
    translation_uses_target_script,
)

SOURCE_LANGUAGES = dict(VideoUrlForm.SOURCE_LANGUAGE_CHOICES)


def _allowed_source_languages(language_mode):
    """The German modes offer German and English captions; any other mode, its own language."""
    source_language = get_source_language(language_mode)
    if source_language == "de":
        return {"de", "en"}
    return {source_language}


# These pages show whether the signed-in account finished today's media task.
private_response = cache_control(private=True, no_store=True)


@private_response
def video_workspace(request):
    language_mode = get_language_mode(request)
    target_language = get_target_language(language_mode)
    default_source = get_source_language(language_mode)
    allowed_sources = _allowed_source_languages(language_mode)
    allow_bilingual = language_mode == Deck.LanguageMode.NL_EN
    preference_key = f"video_bilingual:{request.user.pk}"
    bilingual = allow_bilingual and request.session.get(preference_key, False)
    if (
        allow_bilingual
        and request.method == "GET"
        and request.GET.get("captions") in {"bilingual", "dutch"}
    ):
        bilingual = request.GET["captions"] == "bilingual"
        request.session[preference_key] = bilingual
    selected_video = None
    english_video = None
    english_error = ""
    should_refresh = False
    form = VideoUrlForm(
        request.POST or None,
        allowed_source_languages=allowed_sources,
        default_source_language=default_source,
        allow_bilingual=allow_bilingual,
        initial={"bilingual": bilingual},
    )
    if not form.is_bound:
        form.fields["source_language"].initial = default_source

    if request.method == "POST" and form.is_valid():
        if allow_bilingual:
            bilingual = form.cleaned_data["bilingual"]
            request.session[preference_key] = bilingual
        video_id = form.youtube_id
        source_language = form.cleaned_data["source_language"]
        selected_video = TranscriptVideo.objects.filter(
            youtube_id=video_id,
            source_language=source_language,
        ).first()
        should_refresh = request.POST.get("refresh") == "1"

        if selected_video is None or should_refresh:
            try:
                transcript = fetch_transcript(video_id, languages=(source_language,))
            except TranscriptUnavailable as exc:
                form.add_error("url", str(exc))
            else:
                selected_video, _created = TranscriptVideo.objects.update_or_create(
                    youtube_id=video_id,
                    source_language=source_language,
                    defaults={
                        "source_url": canonical_youtube_url(video_id),
                        "transcript": transcript,
                        "caption_origin": "unknown",
                        "caption_language": source_language,
                    },
                )

    if request.method == "GET":
        video_id = request.GET.get("video", "")
        source_language = request.GET.get("lang", default_source)
        if source_language not in allowed_sources:
            source_language = default_source
        selected_video = TranscriptVideo.objects.filter(
            youtube_id=video_id,
            source_language=source_language,
        ).first()
        if selected_video:
            form = VideoUrlForm(
                initial={
                    "url": selected_video.source_url,
                    "source_language": selected_video.source_language,
                    "bilingual": bilingual,
                },
                allowed_source_languages=allowed_sources,
                default_source_language=default_source,
                allow_bilingual=allow_bilingual,
            )
        elif request.GET.get("autofetch") == "1" and video_id:
            form = VideoUrlForm(
                initial={
                    "url": canonical_youtube_url(video_id),
                    "source_language": source_language,
                    "bilingual": bilingual,
                },
                allowed_source_languages=allowed_sources,
                default_source_language=default_source,
                allow_bilingual=allow_bilingual,
            )
            try:
                transcript = fetch_transcript(video_id, languages=(source_language,))
            except TranscriptUnavailable as exc:
                form.add_error("url", str(exc))
            else:
                selected_video, _created = TranscriptVideo.objects.update_or_create(
                    youtube_id=video_id,
                    source_language=source_language,
                    defaults={
                        "source_url": canonical_youtube_url(video_id),
                        "transcript": transcript,
                        "caption_origin": "unknown",
                        "caption_language": source_language,
                    },
                )

    if bilingual and selected_video and not form.errors:
        english_video = TranscriptVideo.objects.filter(
            youtube_id=selected_video.youtube_id, source_language="en"
        ).first()
        if (
            english_video is None
            or not english_video.transcript
            or should_refresh
            or request.POST.get("retry_english") == "1"
        ):
            try:
                english_data = fetch_english_captions(selected_video.youtube_id)
            except TranscriptUnavailable as exc:
                english_error = str(exc)
            else:
                english_video, _created = TranscriptVideo.objects.update_or_create(
                    youtube_id=selected_video.youtube_id,
                    source_language="en",
                    defaults={"source_url": selected_video.source_url, **english_data},
                )

    if request.method == "POST" and selected_video and not form.errors and not english_error:
        caption_query = "&captions=bilingual" if bilingual else ""
        return redirect(
            f"{request.path}?video={selected_video.youtube_id}"
            f"&lang={selected_video.source_language}{caption_query}"
        )

    active_source_language = (
        selected_video.source_language
        if selected_video
        else (form["source_language"].value() or default_source)
    )
    if active_source_language not in allowed_sources:
        active_source_language = default_source
    if selected_video:
        record_trainer_session(
            request,
            TrainerAttempt.Trainer.VIDEO,
            language_mode,
            selected_video.youtube_id,
        )
    context = {
        "form": form,
        "video": selected_video,
        "transcript": selected_video.transcript if selected_video else [],
        "target_language": target_language,
        "target_language_label": get_target_language_label(language_mode),
        "source_language": active_source_language,
        "source_language_label": SOURCE_LANGUAGES[active_source_language],
        "offered_languages": " or ".join(
            name for code, name in SOURCE_LANGUAGES.items() if code in allowed_sources
        ),
        "media_complete_today": task_is_complete(request.user, "media", language_mode),
        "allow_bilingual": allow_bilingual,
        "bilingual": bilingual,
        "english_transcript": english_video.transcript if english_video else [],
        "english_caption_label": english_video.get_caption_origin_display()
        if english_video
        else "",
        "english_error": english_error,
    }
    return render(request, "video_study/workspace.html", context)


@private_response
def podcast_workspace(request):
    language_mode = get_language_mode(request)
    selected_episode = None
    catalog_error = ""
    transcript_error = ""

    if request.GET.get("refresh") == "1" or (
        implicit_catalog_refresh_enabled() and not PodcastEpisode.objects.exists()
    ):
        try:
            refresh_podcast_catalog()
        except PodcastUnavailable as exc:
            catalog_error = str(exc)

    episodes = list(PodcastEpisode.objects.all()[:50])
    requested_id = request.GET.get("episode", "")
    if requested_id.isdigit():
        selected_episode = PodcastEpisode.objects.filter(pk=int(requested_id)).first()
    if selected_episode is None and episodes:
        selected_episode = episodes[0]

    if selected_episode and not selected_episode.transcript and selected_episode.youtube_id:
        try:
            transcript = fetch_transcript(
                selected_episode.youtube_id,
                languages=("de",),
            )
        except TranscriptUnavailable as exc:
            transcript_error = str(exc)
        else:
            selected_episode.transcript = transcript
            selected_episode.save(update_fields=["transcript"])

    if selected_episode and selected_episode.transcript:
        record_trainer_session(
            request,
            TrainerAttempt.Trainer.PODCAST,
            language_mode,
            str(selected_episode.pk),
        )
    context = {
        "podcast": selected_episode,
        "theater": bool(selected_episode and selected_episode.transcript),
        "podcast_episode": selected_episode,
        "podcast_episodes": episodes,
        "transcript": selected_episode.transcript if selected_episode else [],
        "catalog_error": catalog_error,
        "transcript_error": transcript_error,
        "target_language": get_target_language(language_mode),
        "target_language_label": get_target_language_label(language_mode),
        "source_language": "de",
        "source_language_label": "German",
        "media_complete_today": task_is_complete(request.user, "media", language_mode),
    }
    return render(request, "podcasts/workspace.html", context)


@require_POST
def translate_word(request):
    language_mode = get_language_mode(request)
    target_language = get_target_language(language_mode)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON request."}, status=400)

    try:
        normalized_selection = normalize_selection(payload.get("text", payload.get("word", "")))
    except TranslationUnavailable as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    source_language = payload.get("source_language", get_source_language(language_mode))
    if source_language not in SOURCE_LANGUAGES:
        return JsonResponse({"error": "Unsupported source language."}, status=400)
    if source_language not in _allowed_source_languages(language_mode):
        return JsonResponse(
            {"error": "Source language does not match the active study mode."}, status=400
        )

    context = " ".join(str(payload.get("context", "")).split()).strip()
    source_form = " ".join(str(payload.get("text", payload.get("word", ""))).split()).strip()

    budget_start = time.monotonic()

    resolver, genders = capture_for_language(source_language)
    resolution = resolver(source_form, context, budget_start=budget_start)
    lemma = resolution.lemma
    suggestion = {
        "word": lemma,
        "normalized_word": lemma,
        "article": resolution.article,
        "gender": genders.get(resolution.article, ""),
        "plural": resolution.plural,
        "part_of_speech": resolution.part_of_speech,
        "example_sentence": resolution.example_sentence,
        "dictionary_status": resolution.dictionary_status,
        "trustworthy": resolution.trustworthy,
        "source": "dictionary" if resolution.dictionary_status == "found" else "heuristic",
    }
    display_word = (
        f"{resolution.article} {lemma}"
        if resolution.part_of_speech == "noun" and resolution.article
        else lemma
    )

    # Phrases keep the casefolded key: Russian Wiktionary titles are
    # case-sensitive for single words, but a phrase is never sent to it
    # verbatim, so its existing cache rows stay valid.
    is_phrase = resolution.part_of_speech == "phrase"
    translation_key = normalized_selection if is_phrase else lemma
    provider_input = normalized_selection if is_phrase else lemma

    cached = WordTranslation.objects.filter(
        source_text=translation_key,
        source_language=source_language,
        target_language=target_language,
    ).first()
    if cached is None and not is_phrase:
        legacy_key = lemma.casefold()
        if legacy_key != translation_key:
            # Older cache rows hold the casefolded lemma; fall back to reading
            # it so those rows still hit.
            cached = WordTranslation.objects.filter(
                source_text=legacy_key,
                source_language=source_language,
                target_language=target_language,
            ).first()
    cached_text = clean_translation_text(cached.translated_text) if cached else ""
    if cached and translation_uses_target_script(cached_text, target_language):
        if cached_text != cached.translated_text:
            cached.translated_text = cached_text
            cached.save(update_fields=["translated_text", "updated_at"])
        return JsonResponse(
            {
                "word": display_word,
                "translation": cached_text,
                "cached": True,
                "suggestion": suggestion,
            }
        )

    if resolution.translation:
        translation, _created = WordTranslation.objects.update_or_create(
            source_text=translation_key,
            source_language=source_language,
            target_language=target_language,
            defaults={
                "translated_text": resolution.translation,
                "provider": "wiktionary-en",
            },
        )
        return JsonResponse(
            {
                "word": display_word,
                "translation": translation.translated_text,
                "cached": False,
                "suggestion": suggestion,
            }
        )

    try:
        translated_text = clean_translation_text(
            request_translation(
                provider_input,
                source_language=source_language,
                target_language=target_language,
                source_form=provider_input,
                budget_start=budget_start,
            )
        )
    except TranslationUnavailable as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    translation, _created = WordTranslation.objects.update_or_create(
        source_text=translation_key,
        source_language=source_language,
        target_language=target_language,
        defaults={
            "translated_text": translated_text,
            "provider": "auto",
        },
    )
    return JsonResponse(
        {
            "word": display_word,
            "translation": translation.translated_text,
            "cached": False,
            "suggestion": suggestion,
        }
    )
