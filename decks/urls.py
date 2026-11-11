from django.urls import path

from decks import views

app_name = "decks"

urlpatterns = [
    path("", views.DeckListView.as_view(), name="list"),
    path("new/", views.DeckCreateView.as_view(), name="create"),
    path("<int:pk>/", views.DeckDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.DeckUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.DeckDeleteView.as_view(), name="delete"),
]
