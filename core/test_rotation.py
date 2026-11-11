import datetime

from django.test import SimpleTestCase

from core.rotation import rotating_choice, rotating_index


class RotatingIndexTests(SimpleTestCase):
    def test_index_is_stable_for_the_same_day(self):
        day = datetime.date(2026, 8, 4)
        self.assertEqual(rotating_index(4, day), rotating_index(4, day))

    def test_never_repeats_on_consecutive_days(self):
        day = datetime.date(2026, 1, 1)
        for offset in range(400):
            a = rotating_index(4, day + datetime.timedelta(days=offset))
            b = rotating_index(4, day + datetime.timedelta(days=offset + 1))
            self.assertNotEqual(a, b, f"repeat at offset {offset}")

    def test_every_item_appears_once_per_cycle(self):
        # Any window of N consecutive days should cover all N indices exactly once.
        n = 15
        day = datetime.date(2026, 8, 4)
        for start in range(0, 60):
            window = [rotating_index(n, day + datetime.timedelta(days=start + i)) for i in range(n)]
            self.assertEqual(
                sorted(window), list(range(n)), f"window at start {start} not a full cycle"
            )

    def test_salt_decouples_two_sequences(self):
        # Different salts should not march in lockstep every day.
        day = datetime.date(2026, 8, 4)
        matches = sum(
            rotating_index(15, day + datetime.timedelta(days=i), salt="a")
            == rotating_index(15, day + datetime.timedelta(days=i), salt="b")
            for i in range(60)
        )
        self.assertLess(matches, 30)

    def test_single_item_always_zero(self):
        day = datetime.date(2026, 8, 4)
        self.assertEqual(rotating_index(1, day), 0)


class RotatingChoiceTests(SimpleTestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(rotating_choice([], datetime.date(2026, 8, 4)))

    def test_picks_a_member_and_rotates(self):
        items = ["a", "b", "c"]
        day = datetime.date(2026, 8, 4)
        picks = [rotating_choice(items, day + datetime.timedelta(days=i)) for i in range(3)]
        self.assertEqual(sorted(picks), items)
