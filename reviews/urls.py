from django.urls import path

from reviews import (
    drill_engine,
    drill_views,
    flashcard_views,
    study_views,
    views,
)

app_name = "reviews"

urlpatterns = [
    path("start/", views.start_review, name="start"),
    path("drills/", drill_engine.drills_index, name="drills"),
    path(
        "flashcards/<str:trainer>/start/", flashcard_views.flashcards_start, name="flashcards_start"
    ),
    path("flashcards/<str:trainer>/", flashcard_views.flashcards, name="flashcards"),
    path(
        "flashcards/<str:trainer>/complete/",
        flashcard_views.flashcards_complete,
        name="flashcards_complete",
    ),
    path("study/deck/<int:deck_id>/start/", study_views.start_study, name="start_study"),
    path("study/current/", study_views.study_current, name="study_current"),
    path("study/complete/", study_views.study_complete, name="study_complete"),
    path("study/retry/", study_views.retry_mistakes, name="retry_mistakes"),
    path("study/weak/start/", study_views.start_weak_practice, name="start_weak_practice"),
    path("drill/<slug:slug>/start/", drill_views.drill_start, name="drill_start"),
    path("drill/<slug:slug>/", drill_views.drill_step, name="drill"),
    path("drill/<slug:slug>/complete/", drill_views.drill_complete, name="drill_complete"),
    path("<int:card_id>/", views.review_card, name="review_card"),
]
