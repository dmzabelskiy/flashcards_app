from django.db import models


class Rating(models.TextChoices):
    AGAIN = "again", "Again"
    HARD = "hard", "Hard"
    GOOD = "good", "Good"
    EASY = "easy", "Easy"
