# Scheduler

The MVP scheduler lives in `reviews/scheduler.py` and is intentionally small.

Each review stores a `ReviewLog` with the previous due date, new due date, previous interval, new interval, rating, and optional elapsed seconds.

New cards:

- Again: 1 day
- Hard: 1 day
- Good: 3 days
- Easy: 5 days

Reviewed cards:

- Again: interval becomes 1 day, `lapse_count` increases, ease decreases
- Hard: interval grows modestly, ease decreases
- Good: interval grows by the current ease factor
- Easy: interval grows faster, ease increases

The ease factor is clamped to a minimum of 1.3.

