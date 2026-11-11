from django.test import SimpleTestCase

from reviews.normalisers import NORMALISERS, matches


class SpanishNormaliserTests(SimpleTestCase):
    def check(self, typed, accepted):
        return matches(typed, [accepted], NORMALISERS["spanish"])

    def test_case_is_ignored(self):
        self.assertTrue(self.check("Está", "está"))
        self.assertTrue(self.check("él", "él"))

    def test_a_missing_accent_does_not_match(self):
        self.assertFalse(self.check("esta", "está"))
        self.assertFalse(self.check("habló", "hablo"))

    def test_enye_and_dieresis_are_significant(self):
        self.assertFalse(self.check("ano", "año"))
        self.assertFalse(self.check("pinguino", "pingüino"))

    def test_decomposed_accent_equals_the_composed_letter(self):
        self.assertTrue(self.check("é", "é"))
        self.assertTrue(self.check("está", "está"))
        self.assertTrue(self.check("ñ", "ñ"))

    def test_whitespace_is_trimmed_and_collapsed(self):
        self.assertTrue(self.check("  me   gustan ", "me gustan"))

    def test_punctuation_at_both_ends_is_removed(self):
        self.assertTrue(self.check("¿Está?", "está"))
        self.assertTrue(self.check("¡Hola!", "hola"))
        self.assertTrue(self.check("a ti.", "a ti"))

    def test_inner_punctuation_stays(self):
        self.assertFalse(self.check("a, ti", "a ti"))
