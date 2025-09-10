import unittest
from types import SimpleNamespace

import autotrade_settings


class FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value):
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)


class TestAutotradeSettings(unittest.TestCase):
    def setUp(self):
        # Patch get_redis to return our fake client
        self.fake = FakeRedis()
        autotrade_settings.get_redis = lambda: self.fake
        # ensure clean state
        self.user_id = 12345
        key = f"autotrade:settings:{self.user_id}"
        if key in self.fake.store:
            del self.fake.store[key]

    def test_validate_and_set_success(self):
        ok, msg = autotrade_settings.validate_and_set(self.user_id, "rsi_buy", "35")
        self.assertTrue(ok)
        self.assertIn("set to", msg)
        # confirm persisted
        stored = autotrade_settings.get_user_settings(self.user_id)
        self.assertEqual(stored.get("rsi_buy"), 35.0)

    def test_validate_and_set_invalid_key(self):
        ok, msg = autotrade_settings.validate_and_set(self.user_id, "no_such", "1")
        self.assertFalse(ok)
        self.assertIn("Unknown setting", msg)

    def test_validate_and_set_type_error(self):
        ok, msg = autotrade_settings.validate_and_set(self.user_id, "trade_size", "abc")
        self.assertFalse(ok)
        self.assertIn("Validation failed", msg)

    def test_validate_and_set_out_of_range(self):
        ok, msg = autotrade_settings.validate_and_set(self.user_id, "stop_loss", "0.1")
        self.assertFalse(ok)
        self.assertIn("value too small", msg)

    def test_trailing_interfield_validation(self):
        # set trailing_activation low then try to set trailing_drop >= activation
        ok, msg = autotrade_settings.validate_and_set(
            self.user_id, "trailing_activation", "3"
        )
        self.assertTrue(ok)
        ok2, msg2 = autotrade_settings.validate_and_set(
            self.user_id, "trailing_drop", "3"
        )
        self.assertFalse(ok2)
        self.assertIn("trailing_drop must be less than trailing_activation", msg2)

    def test_reset_setting(self):
        ok, msg = autotrade_settings.validate_and_set(self.user_id, "rsi_buy", "32")
        self.assertTrue(ok)
        ok2, msg2 = autotrade_settings.reset_setting(self.user_id, "rsi_buy")
        self.assertTrue(ok2)
        stored = autotrade_settings.get_user_settings(self.user_id)
        self.assertNotIn("rsi_buy", stored)

    def test_export_csv(self):
        # default export should include keys
        csv = autotrade_settings.export_settings_csv(self.user_id)
        self.assertIn("rsi_buy", csv)
        self.assertIn("trade_size", csv)


if __name__ == "__main__":
    unittest.main()
