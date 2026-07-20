import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from omar_ai_core import diagnostic_export


class DiagnosticExportTests(unittest.TestCase):
    def tearDown(self):
        diagnostic_export.stop_diagnostic_share()

    def test_redaction_removes_known_and_inline_credentials(self):
        with patch.object(
            diagnostic_export,
            "get_config",
            return_value={"HOME_ASSISTANT_TOKEN": "private-token-123"},
        ):
            result = diagnostic_export.redact_diagnostic_text(
                "HOME_ASSISTANT_TOKEN=private-token-123 "
                "GEMINI_API_KEY=AIzaabcdefghijklmnopqrstuvwxyz123456 "
                "Authorization: Bearer abc.def.ghi"
            )
        self.assertNotIn("private-token-123", result)
        self.assertNotIn("AIzaabcdefghijklmnopqrstuvwxyz123456", result)
        self.assertNotIn("abc.def.ghi", result)

    def test_report_includes_redacted_conversation_context(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            diagnostic_export, "REPORT_DIR", Path(folder)
        ), patch.object(
            diagnostic_export,
            "read_diagnostics",
            return_value="Traceback: fallo técnico",
        ), patch.object(
            diagnostic_export,
            "read_history",
            return_value="You: contexto necesario\nJarvis: respuesta",
        ), patch.object(diagnostic_export, "get_config", return_value={}):
            report = diagnostic_export.create_diagnostic_report({"state": "ERROR"})
            content = report.read_text(encoding="utf-8")
        self.assertIn("contexto necesario", content)
        self.assertIn("Jarvis: respuesta", content)
        self.assertIn("Traceback: fallo técnico", content)
        self.assertIn('"state": "ERROR"', content)

    def test_temporary_link_downloads_only_the_report(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            diagnostic_export, "_local_ipv4", return_value="127.0.0.1"
        ):
            report = Path(folder) / "diagnostico.txt"
            report.write_text("informe seguro", encoding="utf-8")
            shared = diagnostic_export.share_diagnostic_report(report, expires_seconds=5)
            with urllib.request.urlopen(shared["url"], timeout=2) as response:
                self.assertEqual(response.read().decode("utf-8"), "informe seguro")


if __name__ == "__main__":
    unittest.main()
