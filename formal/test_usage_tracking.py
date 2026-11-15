import unittest

from check_usage_tracking import parse_environment, require


class UsageEnvironment(unittest.TestCase):
    def test_parses_documented_values_and_quoted_paths(self):
        text = 'CPA_BASE_URL=http://127.0.0.1:8317\nAPP_PORT=8318\nAUTH_ENABLED=true\n\n# Local collector\nWORK_DIR="/Users/Alex Smith/private" # Storage\n'
        self.assertEqual(parse_environment(text), {
            "CPA_BASE_URL": "http://127.0.0.1:8317",
            "APP_PORT": "8318",
            "AUTH_ENABLED": "true",
            "WORK_DIR": "/Users/Alex Smith/private",
        })

    def test_rejects_a_line_without_an_assignment(self):
        with self.assertRaises(ValueError):
            parse_environment("APP_PORT")

    def test_rejects_an_invalid_deployment_setting(self):
        with self.assertRaisesRegex(ValueError, "Login protection required"):
            require(False, "Login protection required")


if __name__ == "__main__":
    unittest.main()
