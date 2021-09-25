from contextlib import contextmanager
import os
import shutil
import tempfile
import unittest

from clcache.utils import (
    normalizeBaseDir,
    copyOrLink,
)


# TODO: should be shared between unit tests
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
@contextmanager
def cd(targetDirectory):
    oldDirectory = os.getcwd()
    os.chdir(os.path.expanduser(targetDirectory))
    try:
        yield
    finally:
        os.chdir(oldDirectory)

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


class TestCopyCompression(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory
        self.testDir = tempfile.mkdtemp()

    def tearDown(self):
        # Remove the directory after the test
        shutil.rmtree(self.testDir)
        os.environ.clear()

    def assertEntrySizeIsCorrect(self, expectedSize):
        with cd(self.testDir):
            srcFilePath = os.path.join(self.testDir, "src")
            dstFilePath = os.path.join(self.testDir, "dst")
            with open(srcFilePath, "wb") as f:
                for i in range(0, 999):
                    f.write(b"%d" % i)
            copyOrLink(srcFilePath, dstFilePath, True)
            size = os.path.getsize(dstFilePath)
            self.assertEqual(size, expectedSize)

    def testCompression(self):
        os.environ["CLCACHE_COMPRESS"] = "1"
        self.assertEntrySizeIsCorrect(1481)

    def testCompressionLevel(self):
        os.environ["CLCACHE_COMPRESS"] = "1"
        os.environ["CLCACHE_COMPRESSLEVEL"] = "1"
        self.assertEntrySizeIsCorrect(1536)

    def testNoCompression(self):
        self.assertEntrySizeIsCorrect(2887)

    def testDecompression(self):
        os.environ["CLCACHE_COMPRESS"] = "1"
        with cd(self.testDir):
            srcFilePath = os.path.join(self.testDir, "src")
            tmpFilePath = os.path.join(self.testDir, "tmp")
            dstFilePath = os.path.join(self.testDir, "dst")
            with open(srcFilePath, "wb") as f:
                f.write(b"Content")
            copyOrLink(srcFilePath, tmpFilePath, True)
            copyOrLink(tmpFilePath, dstFilePath)
            self.assertNotEqual(os.path.getsize(srcFilePath), os.path.getsize(tmpFilePath))
            self.assertEqual(os.path.getsize(srcFilePath), os.path.getsize(dstFilePath))
