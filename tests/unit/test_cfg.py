import tempfile
import unittest

from clcache.cfg import Configuration


def temporaryFileName():
    with tempfile.NamedTemporaryFile() as f:
        return f.name


class TestConfiguration(unittest.TestCase):
    def testOpenClose(self):
        with Configuration(temporaryFileName()):
            pass

    def testDefaults(self):
        with Configuration(temporaryFileName()) as cfg:
            self.assertGreaterEqual(cfg.maximumCacheSize(), 1024) # 1KiB
