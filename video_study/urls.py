from django.urls import path

from video_study import views

app_name = "video_study"

urlpatterns = [
    path("", views.video_workspace, name="workspace"),
    path("translate/", views.translate_word, name="translate_word"),
]
