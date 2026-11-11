from django.urls import path

from core import completion_views, views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("stats/", views.stats, name="stats"),
    path("mode/", views.set_language_mode_view, name="set_language_mode"),
    path("complete/media/", completion_views.complete_media, name="complete_media"),
]
