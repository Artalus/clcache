import os
import unittest
import tempfile

from clcache.manifest import (
    Manifest,
    ManifestEntry,
)
from clcache.clcache import (
    filesBeneath,
    createManifestEntry,
)


class TestManifest(unittest.TestCase):
    entry1 = ManifestEntry([r'somepath\myinclude.h'],
                           "fdde59862785f9f0ad6e661b9b5746b7",
                           "a649723940dc975ebd17167d29a532f8")
    entry2 = ManifestEntry([r'somepath\myinclude.h', r'moreincludes.h'],
                           "474e7fc26a592d84dfa7416c10f036c6",
                           "8771d7ebcf6c8bd57a3d6485f63e3a89")
    entries = [entry1, entry2]

    def testCreateEmpty(self):
        manifest = Manifest()
        self.assertFalse(manifest.entries())

    def testCreateWithEntries(self):
        manifest = Manifest(TestManifest.entries)
        self.assertEqual(TestManifest.entries, manifest.entries())


    def testAddEntry(self):
        manifest = Manifest(TestManifest.entries)
        newEntry = ManifestEntry([r'somepath\myotherinclude.h'],
                                 "474e7fc26a592d84dfa7416c10f036c6",
                                 "8771d7ebcf6c8bd57a3d6485f63e3a89")
        manifest.addEntry(newEntry)
        self.assertEqual(newEntry, manifest.entries()[0])


    def testTouchEntry(self):
        manifest = Manifest(TestManifest.entries)
        self.assertEqual(TestManifest.entry1, manifest.entries()[0])
        manifest.touchEntry("8771d7ebcf6c8bd57a3d6485f63e3a89")
        self.assertEqual(TestManifest.entry2, manifest.entries()[0])


class TestCreateManifestEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempDir = tempfile.TemporaryDirectory()
        for i in range(10):
            sampleName = 'sample{}.h'.format(i)
            filePath = os.path.join(cls.tempDir.name, '{}.h'.format(sampleName))
            with open(filePath, 'w') as f:
                f.write('#define {}'.format(sampleName))

        cls.includePaths = list(sorted(filesBeneath(cls.tempDir.name)))
        cls.manifestHash = 'ffffffffffffffffffffffffffffffff'
        cls.expectedManifestEntry = createManifestEntry(TestCreateManifestEntry.manifestHash,
                                                                TestCreateManifestEntry.includePaths)

    @classmethod
    def tearDownClass(cls):
        cls.tempDir.cleanup()

    def assertManifestEntryIsCorrect(self, entry):
        self.assertEqual(entry.includesContentHash, TestCreateManifestEntry.expectedManifestEntry.includesContentHash)
        self.assertEqual(entry.objectHash, TestCreateManifestEntry.expectedManifestEntry.objectHash)
        self.assertEqual(entry.includeFiles, TestCreateManifestEntry.expectedManifestEntry.includeFiles)

    def testIsConsistentWithSameInput(self):
        entry = createManifestEntry(TestCreateManifestEntry.manifestHash, TestCreateManifestEntry.includePaths)
        self.assertManifestEntryIsCorrect(entry)

    def testIsConsistentWithReverseList(self):
        reversedIncludePaths = list(reversed(TestCreateManifestEntry.includePaths))
        entry = createManifestEntry(TestCreateManifestEntry.manifestHash, reversedIncludePaths)
        self.assertManifestEntryIsCorrect(entry)

    def testIsConsistentWithDuplicateEntries(self):
        includePathsWithDuplicates = TestCreateManifestEntry.includePaths + TestCreateManifestEntry.includePaths
        entry = createManifestEntry(TestCreateManifestEntry.manifestHash, includePathsWithDuplicates)
        self.assertManifestEntryIsCorrect(entry)
