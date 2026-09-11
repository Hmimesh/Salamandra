import tempfile
import unittest
from pathlib import Path

from e2e_server import handler_for
from accounts import AccountStore
from database import MembershipModel, SessionModel, UserModel
from sqlalchemy import func, select


class TestBrowserFixture(unittest.TestCase):
    def test_seed_hash_patch_is_removed_and_real_authentication_is_required(self):
        original_hash = AccountStore.hash_password
        with tempfile.TemporaryDirectory(prefix="salamandra-fixture-test-") as directory:
            handler = handler_for(Path(directory), 4173)
            runtime = handler.database_runtime
            try:
                self.assertIs(AccountStore.hash_password, original_hash)
                self.assertIsNone(runtime.accounts.authenticate("owner@playwright.test", "wrong-password"))
                owner = runtime.accounts.authenticate("owner@playwright.test", "playwright-password")
                self.assertEqual(owner.role, "owner")
                with runtime.factory() as session:
                    self.assertEqual(session.scalar(select(func.count()).select_from(UserModel)), 22)
                    self.assertEqual(session.scalar(select(func.count()).select_from(MembershipModel)), 22)
                    self.assertEqual(session.scalar(select(func.count()).select_from(SessionModel)), 0)
                    self.assertTrue(all(row.password_hash.startswith("$argon2") for row in session.scalars(select(UserModel))))
            finally:
                runtime.factory.kw["bind"].dispose()
