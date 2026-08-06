import unittest

from app import register, safe_parse


class TestApp(unittest.TestCase):
    def test_register_uses_fresh_list(self):
        first = register()
        second = register()
        self.assertIsNot(first, second)

    def test_safe_parse_handles_invalid(self):
        self.assertEqual(safe_parse("12"), 12)
        self.assertEqual(safe_parse("oops"), 0)


if __name__ == "__main__":
    unittest.main()
