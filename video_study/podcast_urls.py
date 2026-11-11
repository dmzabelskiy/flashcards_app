from django.urls import path

from video_study import views

app_name = "podcasts"

urlpatterns = [
    path("", views.podcast_workspace, name="workspace"),
]
