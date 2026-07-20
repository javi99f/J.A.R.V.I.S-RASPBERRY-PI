import unittest

from tools.build_pi_release import release_notes_for_version


class ReleaseNotesTests(unittest.TestCase):
    def test_only_requested_version_is_published(self):
        changelog = """# Historial

## Cambios para la version 0.1.1

- Corrección uno.
- Corrección dos.

## Cambios para la version 0.1.0

- Base estable.
"""
        notes = release_notes_for_version(changelog, "0.1.1")
        self.assertTrue(notes.startswith("## Cambios para la version 0.1.1"))
        self.assertIn("- Corrección uno.", notes)
        self.assertNotIn("0.1.0", notes)

    def test_missing_version_section_is_rejected(self):
        with self.assertRaises(SystemExit):
            release_notes_for_version("## Cambios para la version 0.1.0\n\n- Base.", "0.1.1")


if __name__ == "__main__":
    unittest.main()
