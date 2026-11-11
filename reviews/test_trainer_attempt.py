from django.contrib import admin
from django.contrib.admin.utils import get_deleted_objects
from django.test import RequestFactory
from django.utils import timezone

from core.testutils import AuthedTestCase, create_user
from reviews.models import TrainerAttempt


class TrainerAttemptStrTests(AuthedTestCase):
    def attempt(self, trainer, label="Item"):
        return TrainerAttempt.objects.create(
            owner=self.user, trainer=trainer, item_label=label, occurred_at=timezone.now()
        )

    def test_a_current_drill_uses_the_registry_label(self):
        self.assertEqual(str(self.attempt("verb_preposition")), "Verb + preposition · Item")

    def test_a_retired_trainer_uses_the_legacy_label(self):
        self.assertEqual(str(self.attempt("grammar")), "Grammar book · Item")

    def test_an_unknown_trainer_falls_back_to_the_stored_name(self):
        self.assertEqual(
            str(self.attempt("no_such_trainer", label="")), "no_such_trainer · session"
        )

    def test_the_admin_deletion_preview_lists_a_users_attempts(self):
        self.attempt("irregular_verb")
        self.attempt("telc_b2")
        self.attempt("no_such_trainer")
        admin_user = create_user("admin-user", is_staff=True, is_superuser=True)
        request = RequestFactory().get("/admin/")
        request.user = admin_user

        to_delete, _model_count, _perms, protected = get_deleted_objects(
            [self.user], request, admin.site
        )

        self.assertEqual(protected, [])
        self.assertIn("Irregular verbs · Item", str(to_delete))
