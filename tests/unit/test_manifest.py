from contextlib import contextmanager
import os
import unittest
import tempfile

from clcache.manifest import (
    Manifest,
    ManifestEntry,
    ManifestRepository,
    filesBeneath,
)
from clcache.clcache import (
    createManifestEntry,
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


class TestManifestRepository(unittest.TestCase):
    entry1 = ManifestEntry([r'somepath\myinclude.h'],
                           "fdde59862785f9f0ad6e661b9b5746b7",
                           "a649723940dc975ebd17167d29a532f8")
    entry2 = ManifestEntry([r'somepath\myinclude.h', r'moreincludes.h'],
                           "474e7fc26a592d84dfa7416c10f036c6",
                           "8771d7ebcf6c8bd57a3d6485f63e3a89")
    # Size in (120, 240] bytes
    manifest1 = Manifest([entry1])
    # Size in (120, 240] bytes
    manifest2 = Manifest([entry2])

    def _getDirectorySize(self, dirPath):
        def filesize(path, filename):
            return os.stat(os.path.join(path, filename)).st_size

        size = 0
        for path, _, filenames in os.walk(dirPath):
            size += sum(filesize(path, f) for f in filenames)

        return size

    def testPaths(self):
        manifestsRootDir = os.path.join(ASSETS_DIR, "manifests")
        mm = ManifestRepository(manifestsRootDir)
        ms = mm.section("fdde59862785f9f0ad6e661b9b5746b7")

        self.assertEqual(ms.manifestSectionDir, os.path.join(manifestsRootDir, "fd"))
        self.assertEqual(ms.manifestPath("fdde59862785f9f0ad6e661b9b5746b7"),
                         os.path.join(manifestsRootDir, "fd", "fdde59862785f9f0ad6e661b9b5746b7.json"))

    def testIncludesContentHash(self):
        self.assertEqual(
            ManifestRepository.getIncludesContentHashForHashes([]),
            ManifestRepository.getIncludesContentHashForHashes([])
        )

        self.assertEqual(
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf"]),
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf"])
        )

        self.assertEqual(
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf", "f6c8bd5733"]),
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf", "f6c8bd5733"])
        )

        # Wrong number of elements
        self.assertNotEqual(
            ManifestRepository.getIncludesContentHashForHashes([]),
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf"])
        )

        # Wrong order
        self.assertNotEqual(
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf", "f6c8bd5733"]),
            ManifestRepository.getIncludesContentHashForHashes(["f6c8bd5733", "d88be7edbf"])
        )

        # Content in different elements
        self.assertNotEqual(
            ManifestRepository.getIncludesContentHashForHashes(["", "d88be7edbf"]),
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf", ""])
        )
        self.assertNotEqual(
            ManifestRepository.getIncludesContentHashForHashes(["d88be", "7edbf"]),
            ManifestRepository.getIncludesContentHashForHashes(["d88b", "e7edbf"])
        )

    def testStoreAndGetManifest(self):
        with tempfile.TemporaryDirectory() as manifestsRootDir:
            mm = ManifestRepository(manifestsRootDir)

            ms1 = mm.section("8a33738d88be7edbacef48e262bbb5bc")
            ms2 = mm.section("0623305942d216c165970948424ae7d1")

            ms1.setManifest("8a33738d88be7edbacef48e262bbb5bc", TestManifestRepository.manifest1)
            ms2.setManifest("0623305942d216c165970948424ae7d1", TestManifestRepository.manifest2)

            retrieved1 = ms1.getManifest("8a33738d88be7edbacef48e262bbb5bc")
            self.assertIsNotNone(retrieved1)
            retrieved1Entry = retrieved1.entries()[0]
            self.assertEqual(retrieved1Entry, TestManifestRepository.entry1)

            retrieved2 = ms2.getManifest("0623305942d216c165970948424ae7d1")
            self.assertIsNotNone(retrieved2)
            retrieved2Entry = retrieved2.entries()[0]
            self.assertEqual(retrieved2Entry, TestManifestRepository.entry2)

    def testNonExistingManifest(self):
        manifestsRootDir = os.path.join(ASSETS_DIR, "manifests")
        mm = ManifestRepository(manifestsRootDir)

        retrieved = mm.section("ffffffffffffffffffffffffffffffff").getManifest("ffffffffffffffffffffffffffffffff")
        self.assertIsNone(retrieved)

    def testBrokenManifest(self):
        manifestsRootDir = os.path.join(ASSETS_DIR, "manifests")
        mm = ManifestRepository(manifestsRootDir)

        retrieved = mm.section("brokenmanifest").getManifest("brokenmanifest")
        self.assertIsNone(retrieved)

    def testClean(self):
        with tempfile.TemporaryDirectory() as manifestsRootDir:
            mm = ManifestRepository(manifestsRootDir)

            mm.section("8a33738d88be7edbacef48e262bbb5bc").setManifest("8a33738d88be7edbacef48e262bbb5bc",
                                                                       TestManifestRepository.manifest1)
            mm.section("0623305942d216c165970948424ae7d1").setManifest("0623305942d216c165970948424ae7d1",
                                                                       TestManifestRepository.manifest2)

            cleaningResultSize = mm.clean(240)
            # Only one of those manifests can be left
            self.assertLessEqual(cleaningResultSize, 240)
            self.assertLessEqual(self._getDirectorySize(manifestsRootDir), 240)

            cleaningResultSize = mm.clean(240)
            # The one remaining is remains alive
            self.assertLessEqual(cleaningResultSize, 240)
            self.assertGreaterEqual(cleaningResultSize, 120)
            self.assertLessEqual(self._getDirectorySize(manifestsRootDir), 240)
            self.assertGreaterEqual(self._getDirectorySize(manifestsRootDir), 120)

            cleaningResultSize = mm.clean(0)
            # All manifest are gone
            self.assertEqual(cleaningResultSize, 0)
            self.assertEqual(self._getDirectorySize(manifestsRootDir), 0)


class TestFilesBeneath(unittest.TestCase):
    def testFilesBeneathSimple(self):
        with cd(os.path.join(ASSETS_DIR, "files-beneath")):
            files = list(filesBeneath("a"))
            self.assertEqual(len(files), 2)
            self.assertIn(r"a\1.txt", files)
            self.assertIn(r"a\2.txt", files)

    def testFilesBeneathDeep(self):
        with cd(os.path.join(ASSETS_DIR, "files-beneath")):
            files = list(filesBeneath("b"))
            self.assertEqual(len(files), 1)
            self.assertIn(r"b\c\3.txt", files)

    def testFilesBeneathRecursive(self):
        with cd(os.path.join(ASSETS_DIR, "files-beneath")):
            files = list(filesBeneath("."))
            self.assertEqual(len(files), 5)
            self.assertIn(r".\a\1.txt", files)
            self.assertIn(r".\a\2.txt", files)
            self.assertIn(r".\b\c\3.txt", files)
            self.assertIn(r".\d\4.txt", files)
            self.assertIn(r".\d\e\5.txt", files)
