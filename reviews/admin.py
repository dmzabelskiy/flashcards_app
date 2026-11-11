from django.contrib import admin

from reviews.models import DailyStudyProgress, ReviewLog


@admin.register(ReviewLog)
class ReviewLogAdmin(admin.ModelAdmin):
    list_display = ["card", "rating", "reviewed_at", "previous_interval_days", "new_interval_days"]
    list_filter = ["rating", "reviewed_at"]
    search_fields = ["card__front", "card__back"]


@admin.register(DailyStudyProgress)
class DailyStudyProgressAdmin(admin.ModelAdmin):
    list_display = ["deck", "date", "correct_count", "updated_at"]
    list_filter = ["date", "deck"]
    search_fields = ["deck__name"]
