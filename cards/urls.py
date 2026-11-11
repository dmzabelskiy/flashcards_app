from django.urls import path

from cards import global_views, views

app_name = "cards"

urlpatterns = [
    path("global/add/", global_views.add_to_global_deck, name="add_to_global"),
    path("global/<int:pk>/retire/", global_views.retire_global_card, name="retire_global_card"),
    path("", views.CardBrowseView.as_view(), name="list"),
    path("new/", views.CardCreateView.as_view(), name="create"),
    path("<int:pk>/", views.CardDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.CardUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.CardDeleteView.as_view(), name="delete"),
    path("deck/<int:deck_id>/export.csv", views.export_deck_csv, name="export_deck_csv"),
]
