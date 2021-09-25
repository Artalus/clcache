import unittest
from clcache.utils import normalizeBaseDir

class TestUtils(unittest.TestCase):
    def testNormalizeBaseDir(self):
        self.assertIsNone(normalizeBaseDir(None))
        self.assertIsNone(normalizeBaseDir(r""))

        # Note: raw string literals cannot end in an odd number of backslashes
        # https://docs.python.org/3/faq/design.html#why-can-t-raw-strings-r-strings-end-with-a-backslash
        # So we consistenly use basic literals
        self.assertEqual(normalizeBaseDir("c:"), "c:")
        self.assertEqual(normalizeBaseDir("c:\\projects"), "c:\\projects")

        self.assertEqual(normalizeBaseDir("C:\\"), "c:")
        self.assertEqual(normalizeBaseDir("C:\\Projects\\"), "c:\\projects")

        self.assertEqual(normalizeBaseDir("c:\\projects with space"), "c:\\projects with space")
        self.assertEqual(normalizeBaseDir("c:\\projects with ö"), "c:\\projects with ö")
