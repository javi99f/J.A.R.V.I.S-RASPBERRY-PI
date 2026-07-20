import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class PiRequirementsTests(unittest.TestCase):
    def test_python_313_updater_does_not_resolve_tflite_runtime_again(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn(
            'openwakeword==0.6.0; python_version < "3.13"',
            requirements,
        )


if __name__ == "__main__":
    unittest.main()
