from django.urls import path

from imports_app import views

app_name = "imports_app"

urlpatterns = [
    path("", views.import_cards, name="import_cards"),
]
