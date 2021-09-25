import os
import tempfile
import unittest

from clcache.stats import (
    Statistics,
    PersistentJSONDict,
)

# TODO: should be shared between unit tests
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
def temporaryFileName():
    with tempfile.NamedTemporaryFile() as f:
        return f.name


class TestPersistentJSONDict(unittest.TestCase):
    def testEmptyFile(self):
        emptyFile = os.path.join(ASSETS_DIR, "empty_file.txt")
        PersistentJSONDict(emptyFile)

    def testBrokenJson(self):
        brokenJson = os.path.join(ASSETS_DIR, "broken_json.txt")
        PersistentJSONDict(brokenJson)



class TestStatistics(unittest.TestCase):
    def testOpenClose(self):
        with Statistics(temporaryFileName()):
            pass

    def testHitCounts(self):
        with Statistics(temporaryFileName()) as s:
            self.assertEqual(s.numCallsWithInvalidArgument(), 0)
            self.assertEqual(s.numCallsWithoutSourceFile(), 0)
            self.assertEqual(s.numCallsWithMultipleSourceFiles(), 0)
            self.assertEqual(s.numCallsWithPch(), 0)
            self.assertEqual(s.numCallsForLinking(), 0)
            self.assertEqual(s.numCallsForExternalDebugInfo(), 0)
            self.assertEqual(s.numEvictedMisses(), 0)
            self.assertEqual(s.numHeaderChangedMisses(), 0)
            self.assertEqual(s.numSourceChangedMisses(), 0)
            self.assertEqual(s.numCacheHits(), 0)
            self.assertEqual(s.numCacheMisses(), 0)
            self.assertEqual(s.numCallsForPreprocessing(), 0)

            # Bump all by 1
            s.registerCallWithInvalidArgument()
            s.registerCallWithoutSourceFile()
            s.registerCallWithMultipleSourceFiles()
            s.registerCallWithPch()
            s.registerCallForLinking()
            s.registerCallForExternalDebugInfo()
            s.registerEvictedMiss()
            s.registerHeaderChangedMiss()
            s.registerSourceChangedMiss()
            s.registerCacheHit()
            s.registerCacheMiss()
            s.registerCallForPreprocessing()

            self.assertEqual(s.numCallsWithInvalidArgument(), 1)
            self.assertEqual(s.numCallsWithoutSourceFile(), 1)
            self.assertEqual(s.numCallsWithMultipleSourceFiles(), 1)
            self.assertEqual(s.numCallsWithPch(), 1)
            self.assertEqual(s.numCallsForLinking(), 1)
            self.assertEqual(s.numCallsForExternalDebugInfo(), 1)
            self.assertEqual(s.numEvictedMisses(), 1)
            self.assertEqual(s.numHeaderChangedMisses(), 1)
            self.assertEqual(s.numSourceChangedMisses(), 1)
            self.assertEqual(s.numCacheHits(), 1)
            self.assertEqual(s.numCallsForPreprocessing(), 1)

            # accumulated: headerChanged, sourceChanged, eviced, miss
            self.assertEqual(s.numCacheMisses(), 4)
