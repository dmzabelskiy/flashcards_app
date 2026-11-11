import datetime


def _level(count):
    if count <= 0:
        return 0
    if count <= 2:
        return 1
    if count <= 5:
        return 2
    if count <= 9:
        return 3
    return 4


def build_activity_calendar(day_counts, today, window_days=365):
    """Turn a {date: count} mapping into GitHub-style weeks plus streaks."""
    start = today - datetime.timedelta(days=window_days - 1)

    # Pad so the first column starts on a Monday.
    grid_start = start - datetime.timedelta(days=start.weekday())
    weeks = []
    current_week = []
    day = grid_start
    while day <= today:
        if day < start:
            current_week.append({"date": None, "count": 0, "level": 0})
        else:
            count = day_counts.get(day, 0)
            current_week.append({"date": day, "count": count, "level": _level(count)})
        if len(current_week) == 7:
            weeks.append(current_week)
            current_week = []
        day += datetime.timedelta(days=1)
    if current_week:
        while len(current_week) < 7:
            current_week.append({"date": None, "count": 0, "level": 0})
        weeks.append(current_week)

    current_streak = 0
    cursor = today
    while day_counts.get(cursor, 0) > 0:
        current_streak += 1
        cursor -= datetime.timedelta(days=1)

    longest_streak = 0
    run = 0
    day = start
    while day <= today:
        if day_counts.get(day, 0) > 0:
            run += 1
            longest_streak = max(longest_streak, run)
        else:
            run = 0
        day += datetime.timedelta(days=1)

    return {"weeks": weeks, "current_streak": current_streak, "longest_streak": longest_streak}
