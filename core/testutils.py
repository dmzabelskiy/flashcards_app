from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


def create_user(username="user", password="pw-testing-123", **extra):
    return User.objects.create_user(username=username, password=password, **extra)


class AuthedTestCase(TestCase):
    """TestCase with a logged-in client and `self.user` available."""

    username = "user"
    password = "pw-testing-123"

    def setUp(self):
        super().setUp()
        self.user = create_user(self.username, self.password)
        self.client.force_login(self.user)
