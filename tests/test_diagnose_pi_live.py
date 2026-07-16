import unittest
from unittest.mock import patch

from tools import diagnose_pi_live as diagnostic


class PiLiveDiagnosticTests(unittest.TestCase):
    def test_scrub_removes_all_supported_key_forms(self):
        key = "AI" + "za" + "A" * 35
        legacy_key = "AQ" + "." + "B" * 35
        with patch.object(diagnostic, "API_KEY", key):
            text = diagnostic.scrub(
                f"wss://example.test?key={key} "
                f"x-goog-api-key: {legacy_key}"
            )

        self.assertNotIn(key, text)
        self.assertNotIn(legacy_key, text)
        self.assertGreaterEqual(text.count("<redacted>"), 2)

    def test_classify_error_categories(self):
        self.assertEqual(
            diagnostic.classify_error(401, "UNAUTHENTICATED", "bad"), "auth"
        )
        self.assertEqual(
            diagnostic.classify_error(404, "NOT_FOUND", "model missing"), "model"
        )
        self.assertEqual(
            diagnostic.classify_error(429, "RESOURCE_EXHAUSTED", "quota"), "quota"
        )
        self.assertEqual(
            diagnostic.classify_error(403, "PERMISSION_DENIED", "API blocked"),
            "permission",
        )
        self.assertEqual(
            diagnostic.classify_error(400, "INVALID_ARGUMENT", "bad setup"),
            "config",
        )
        self.assertEqual(
            diagnostic.classify_error(403, "", "billing account required"),
            "billing",
        )

    def test_diagnosis_separates_raw_websocket_from_sdk(self):
        results = [
            diagnostic.Result("TLS_IPV4", "OK", "ok"),
            diagnostic.Result("WS_RAW_IPV4", "OK", "ok"),
            diagnostic.Result(
                "SDK_MIN_IPV4", "FAIL", "timeout", "websocket_timeout"
            ),
        ]
        with patch.object(diagnostic, "RESULTS", results):
            summary = diagnostic.diagnosis()

        self.assertIn("SDK/transporte Python", summary)

    def test_successful_live_inference_overrides_metadata_404(self):
        results = [
            diagnostic.Result("MODELO", "FAIL", "HTTP 404", "model"),
            diagnostic.Result("WS_RAW_IPV4", "OK", "inferencia OK"),
            diagnostic.Result("SDK_MIN_IPV4", "OK", "setup OK"),
            diagnostic.Result("SDK_JARVIS", "OK", "setup OK"),
        ]
        with patch.object(diagnostic, "RESULTS", results):
            summary = diagnostic.diagnosis()

        self.assertIn("configuracion completa", summary)
        self.assertNotIn("no esta disponible", summary)


if __name__ == "__main__":
    unittest.main()
