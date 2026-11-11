from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        login_not_required(auth_views.LoginView.as_view(template_name="registration/login.html")),
        name="login",
    ),
    path("healthz", login_not_required(core_views.healthz), name="healthz"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("core.urls")),
    path("decks/", include("decks.urls")),
    path("cards/", include("cards.urls")),
    path("reviews/", include("reviews.urls")),
    path("import/", include("imports_app.urls")),
    path("videos/", include("video_study.urls")),
    path("podcasts/", include("video_study.podcast_urls")),
]
