#!/usr/bin/env python
#
# This file is part of the clcache project.
#
# The contents of this file are subject to the BSD 3-Clause License, the
# full text of which is available in the accompanying LICENSE file at the
# root directory of this project.
#
# In Python unittests are always members, not functions. Silence lint in this file.
# pylint: disable=no-self-use
#
from contextlib import contextmanager
import multiprocessing
import os
import unittest
import tempfile
import shutil

from clcache import WALK
from clcache import clcache

from clcache.clcache import (
    CompilerArtifactsRepository,
    Configuration,
    Manifest,
    ManifestEntry,
    ManifestRepository,
)
from clcache.storage import CacheMemcacheStrategy


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


def temporaryFileName():
    with tempfile.NamedTemporaryFile() as f:
        return f.name


class TestHelperFunctions(unittest.TestCase):
    def testNormalizeBaseDir(self):
        self.assertIsNone(clcache.normalizeBaseDir(None))
        self.assertIsNone(clcache.normalizeBaseDir(r""))

        # Note: raw string literals cannot end in an odd number of backslashes
        # https://docs.python.org/3/faq/design.html#why-can-t-raw-strings-r-strings-end-with-a-backslash
        # So we consistenly use basic literals
        self.assertEqual(clcache.normalizeBaseDir("c:"), "c:")
        self.assertEqual(clcache.normalizeBaseDir("c:\\projects"), "c:\\projects")

        self.assertEqual(clcache.normalizeBaseDir("C:\\"), "c:")
        self.assertEqual(clcache.normalizeBaseDir("C:\\Projects\\"), "c:\\projects")

        self.assertEqual(clcache.normalizeBaseDir("c:\\projects with space"), "c:\\projects with space")
        self.assertEqual(clcache.normalizeBaseDir("c:\\projects with ö"), "c:\\projects with ö")

    def testFilesBeneathSimple(self):
        with cd(os.path.join(ASSETS_DIR, "files-beneath")):
            files = list(clcache.filesBeneath("a"))
            self.assertEqual(len(files), 2)
            self.assertIn(r"a\1.txt", files)
            self.assertIn(r"a\2.txt", files)

    def testFilesBeneathDeep(self):
        with cd(os.path.join(ASSETS_DIR, "files-beneath")):
            files = list(clcache.filesBeneath("b"))
            self.assertEqual(len(files), 1)
            self.assertIn(r"b\c\3.txt", files)

    def testFilesBeneathRecursive(self):
        with cd(os.path.join(ASSETS_DIR, "files-beneath")):
            files = list(clcache.filesBeneath("."))
            self.assertEqual(len(files), 5)
            self.assertIn(r".\a\1.txt", files)
            self.assertIn(r".\a\2.txt", files)
            self.assertIn(r".\b\c\3.txt", files)
            self.assertIn(r".\d\4.txt", files)
            self.assertIn(r".\d\e\5.txt", files)


class TestExtendCommandLineFromEnvironment(unittest.TestCase):
    def testEmpty(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment([], {})
        self.assertEqual(cmdLine, [])
        self.assertEqual(env, {})

    def testSimple(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment(['/nologo'], {'USER': 'ab'})
        self.assertEqual(cmdLine, ['/nologo'])
        self.assertEqual(env, {'USER': 'ab'})

    def testPrepend(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment(['/nologo'], {
            'USER': 'ab',
            'CL': '/MP',
        })
        self.assertEqual(cmdLine, ['/MP', '/nologo'])
        self.assertEqual(env, {'USER': 'ab'})

    def testPrependMultiple(self):
        cmdLine, _ = clcache.extendCommandLineFromEnvironment(['INPUT.C'], {
            'CL': r'/Zp2 /Ox /I\INCLUDE\MYINCLS \LIB\BINMODE.OBJ',
        })
        self.assertEqual(cmdLine, ['/Zp2', '/Ox', r'/I\INCLUDE\MYINCLS', r'\LIB\BINMODE.OBJ', 'INPUT.C'])

    def testAppend(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment(['/nologo'], {
            'USER': 'ab',
            '_CL_': 'file.c',
        })
        self.assertEqual(cmdLine, ['/nologo', 'file.c'])
        self.assertEqual(env, {'USER': 'ab'})

    def testAppendPrepend(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment(['/nologo'], {
            'USER': 'ab',
            'CL': '/MP',
            '_CL_': 'file.c',
        })
        self.assertEqual(cmdLine, ['/MP', '/nologo', 'file.c'])
        self.assertEqual(env, {'USER': 'ab'})


class TestConfiguration(unittest.TestCase):
    def testOpenClose(self):
        with Configuration(temporaryFileName()):
            pass

    def testDefaults(self):
        with Configuration(temporaryFileName()) as cfg:
            self.assertGreaterEqual(cfg.maximumCacheSize(), 1024) # 1KiB


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
        for path, _, filenames in WALK(dirPath):
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


class TestCompilerArtifactsRepository(unittest.TestCase):
    def testPaths(self):
        compilerArtifactsRepositoryRootDir = os.path.join(ASSETS_DIR, "compiler-artifacts-repository")
        car = CompilerArtifactsRepository(compilerArtifactsRepositoryRootDir)
        cas = car.section("fdde59862785f9f0ad6e661b9b5746b7")

        # section path
        self.assertEqual(cas.compilerArtifactsSectionDir, os.path.join(compilerArtifactsRepositoryRootDir, "fd"))

        # entry path
        self.assertEqual(cas.cachedObjectName("fdde59862785f9f0ad6e661b9b5746b7"), os.path.join(
            compilerArtifactsRepositoryRootDir, "fd", "fdde59862785f9f0ad6e661b9b5746b7", "object"))


class TestSplitCommandsFile(unittest.TestCase):
    def _genericTest(self, commandLine, expected):
        self.assertEqual(clcache.splitCommandsFile(commandLine), expected)

    def testEmpty(self):
        self._genericTest('', [])

    def testSimple(self):
        self._genericTest('/nologo', ['/nologo'])
        self._genericTest('/nologo /c', ['/nologo', '/c'])
        self._genericTest('/nologo /c -I.', ['/nologo', '/c', '-I.'])

    def testWhitespace(self):
        self._genericTest('-A -B    -C', ['-A', '-B', '-C'])
        self._genericTest('   -A -B -C', ['-A', '-B', '-C'])
        self._genericTest('-A -B -C   ', ['-A', '-B', '-C'])

    def testMicrosoftExamples(self):
        # https://msdn.microsoft.com/en-us/library/17w5ykft.aspx
        self._genericTest(r'"abc" d e', ['abc', 'd', 'e'])
        self._genericTest(r'a\\b d"e f"g h', [r'a\\b', 'de fg', 'h'])
        self._genericTest(r'a\\\"b c d', [r'a\"b', 'c', 'd'])
        self._genericTest(r'a\\\\"b c" d e', [r'a\\b c', 'd', 'e'])

    def testQuotesAroundArgument(self):
        self._genericTest(r'/Fo"C:\out dir\main.obj"', [r'/FoC:\out dir\main.obj'])
        self._genericTest(r'/c /Fo"C:\out dir\main.obj"', ['/c', r'/FoC:\out dir\main.obj'])
        self._genericTest(r'/Fo"C:\out dir\main.obj" /nologo', [r'/FoC:\out dir\main.obj', '/nologo'])
        self._genericTest(r'/c /Fo"C:\out dir\main.obj" /nologo', ['/c', r'/FoC:\out dir\main.obj', '/nologo'])

    def testDoubleQuoted(self):
        self._genericTest(r'"/Fo"something\main.obj""', [r'/Fosomething\main.obj'])
        self._genericTest(r'/c "/Fo"something\main.obj""', ['/c', r'/Fosomething\main.obj'])
        self._genericTest(r'"/Fo"something\main.obj"" /nologo', [r'/Fosomething\main.obj', '/nologo'])
        self._genericTest(r'/c "/Fo"something\main.obj"" /nologo', ['/c', r'/Fosomething\main.obj', '/nologo'])

    def testBackslashBeforeQuote(self):
        # Pathological cases of escaping the quote incorrectly.
        self._genericTest(r'/Fo"C:\out dir\"', [r'/FoC:\out dir"'])
        self._genericTest(r'/c /Fo"C:\out dir\"', ['/c', r'/FoC:\out dir"'])
        self._genericTest(r'/Fo"C:\out dir\" /nologo', [r'/FoC:\out dir" /nologo'])
        self._genericTest(r'/c /Fo"C:\out dir\" /nologo', ['/c', r'/FoC:\out dir" /nologo'])

        # Sane cases of escaping the backslash correctly.
        self._genericTest(r'/Fo"C:\out dir\\"', [r'/FoC:\out dir' + '\\'])
        self._genericTest(r'/c /Fo"C:\out dir\\"', ['/c', r'/FoC:\out dir' + '\\'])
        self._genericTest(r'/Fo"C:\out dir\\" /nologo', [r'/FoC:\out dir' + '\\', r'/nologo'])
        self._genericTest(r'/c /Fo"C:\out dir\\" /nologo', ['/c', r'/FoC:\out dir' + '\\', r'/nologo'])

    def testVyachselavCase(self):
        self._genericTest(
            r'"-IC:\Program files\Some library" -DX=1 -DVERSION=\"1.0\" -I..\.. -I"..\..\lib" -DMYPATH=\"C:\Path\"',
            [
                r'-IC:\Program files\Some library',
                r'-DX=1',
                r'-DVERSION="1.0"',
                r'-I..\..',
                r'-I..\..\lib',
                r'-DMYPATH="C:\Path"'
            ])

    def testLineEndings(self):
        self._genericTest('-A\n-B', ['-A', '-B'])
        self._genericTest('-A\r\n-B', ['-A', '-B'])
        self._genericTest('-A -B\r\n-C -D -E', ['-A', '-B', '-C', '-D', '-E'])

    def testInitialBackslash(self):
        self._genericTest(r'/Fo"C:\out dir\"', [r'/FoC:\out dir"'])
        self._genericTest(r'\foo.cpp', [r'\foo.cpp'])
        self._genericTest(r'/nologo \foo.cpp', [r'/nologo', r'\foo.cpp'])
        self._genericTest(r'\foo.cpp /c', [r'\foo.cpp', r'/c'])


class TestExpandCommandLine(unittest.TestCase):
    def _genericTest(self, commandLine, expected):
        with cd(os.path.join(ASSETS_DIR, "response-files")):
            self.assertEqual(clcache.expandCommandLine(commandLine), expected)

    def testNoResponseFile(self):
        self._genericTest(['-A', '-B'], ['-A', '-B'])

    def testMissingResponseFile(self):
        with self.assertRaises(FileNotFoundError):
            self._genericTest(['-A', '@no_such_file.rsp', '-B'], [])

    def testSingleResponseFile(self):
        self._genericTest(['-A', '@default_encoded.rsp', '-B'], ['-A', '/DPASSWORD=Käse', '/nologo', '-B'])

    def testMultipleResponseFile(self):
        self._genericTest(
            ['-A', '@default_encoded.rsp', '@utf16_encoded.rsp', '-B'],
            ['-A', '/DPASSWORD=Käse', '/nologo', '/DPASSWORD=Фёдор', '/IC:\\Users\\Миха́йлович', '-B']
        )

    def testNestedResponseFiles(self):
        self._genericTest(
            ['-A', '@nested_response_file.rsp', '-B'],
            ['-A', '/O2', '/DSOMETHING=foo', '/DANOTHERTHING=bar', '/nologo', '-B']
        )


class TestFilterSourceFiles(unittest.TestCase):
    def _assertFiltered(self, cmdLine, files, filteredCmdLine):
        # type: (List[str], List[Tuple[str, str]]) -> List[str]
        files = clcache.filterSourceFiles(cmdLine, files)
        self.assertEqual(list(files), filteredCmdLine)

    def testFilterSourceFiles(self):
        self._assertFiltered(
            ['/c', '/EP', '/FoSome.obj', 'main.cpp'], [('main.cpp', '')],
            ['/c', '/EP', '/FoSome.obj'])
        self._assertFiltered(
            ['/c', '/EP', '/FoSome.obj', 'main.cpp'], [('main.cpp', '/Tp')],
            ['/c', '/EP', '/FoSome.obj'])
        self._assertFiltered(
            ['/c', '/EP', '/FoSome.obj', 'main.cpp'], [('main.cpp', '/Tc')],
            ['/c', '/EP', '/FoSome.obj'])
        self._assertFiltered(
            ['/c', '/EP', '/FoSome.obj', '/Tcmain.cpp'], [('main.cpp', '')],
            ['/c', '/EP', '/FoSome.obj'])
        self._assertFiltered(
            ['/c', '/EP', '/FoSome.obj', '/Tcmain.cpp'], [('main.cpp', '-Tc')],
            ['/c', '/EP', '/FoSome.obj'])

class TestMultipleSourceFiles(unittest.TestCase):
    CPU_CORES = multiprocessing.cpu_count()

    def testCpuCuresPlausibility(self):
        # 1 <= CPU_CORES <= 32
        self.assertGreaterEqual(self.CPU_CORES, 1)
        self.assertLessEqual(self.CPU_CORES, 32)

    def testJobCount(self):
        # Basic parsing
        actual = clcache.jobCount(["/MP1"])
        self.assertEqual(actual, 1)
        actual = clcache.jobCount(["/MP100"])
        self.assertEqual(actual, 100)

        # Without optional max process value
        actual = clcache.jobCount(["/MP"])
        self.assertEqual(actual, self.CPU_CORES)

        # Invalid inputs
        actual = clcache.jobCount(["/MP100.0"])
        self.assertEqual(actual, 1)
        actual = clcache.jobCount(["/MP-100"])
        self.assertEqual(actual, 1)
        actual = clcache.jobCount(["/MPfoo"])
        self.assertEqual(actual, 1)

        # Multiple values
        actual = clcache.jobCount(["/MP1", "/MP44"])
        self.assertEqual(actual, 44)
        actual = clcache.jobCount(["/MP1", "/MP44", "/MP"])
        self.assertEqual(actual, self.CPU_CORES)

        # Find /MP in mixed command line
        actual = clcache.jobCount(["/c", "/nologo", "/MP44"])
        self.assertEqual(actual, 44)
        actual = clcache.jobCount(["/c", "/nologo", "/MP44", "mysource.cpp"])
        self.assertEqual(actual, 44)
        actual = clcache.jobCount(["/MP2", "/c", "/nologo", "/MP44", "mysource.cpp"])
        self.assertEqual(actual, 44)
        actual = clcache.jobCount(["/MP2", "/c", "/MP44", "/nologo", "/MP", "mysource.cpp"])
        self.assertEqual(actual, self.CPU_CORES)


class TestParseIncludes(unittest.TestCase):
    def _readSampleFileDefault(self, lang=None):
        if lang == "de":
            filePath = os.path.join(ASSETS_DIR, 'parse-includes', 'compiler_output_lang_de.txt')
            uniqueIncludesCount = 82
        else:
            filePath = os.path.join(ASSETS_DIR, 'parse-includes', 'compiler_output.txt')
            uniqueIncludesCount = 83

        with open(filePath, 'r') as infile:
            return {
                'CompilerOutput': infile.read(),
                'UniqueIncludesCount': uniqueIncludesCount
            }

    def _readSampleFileNoIncludes(self):
        with open(os.path.join(ASSETS_DIR, 'parse-includes', 'compiler_output_no_includes.txt'), 'r') as infile:
            return {
                'CompilerOutput': infile.read(),
                'UniqueIncludesCount': 0
            }

    def testParseIncludesNoStrip(self):
        sample = self._readSampleFileDefault()
        includesSet, newCompilerOutput = clcache.parseIncludesSet(
            sample['CompilerOutput'],
            r'C:\Projects\test\smartsqlite\src\version.cpp',
            strip=False)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\projects\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(
            r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)
        self.assertEqual(newCompilerOutput, sample['CompilerOutput'])

    def testParseIncludesStrip(self):
        sample = self._readSampleFileDefault()
        includesSet, newCompilerOutput = clcache.parseIncludesSet(
            sample['CompilerOutput'],
            r'C:\Projects\test\smartsqlite\src\version.cpp',
            strip=True)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\projects\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(
            r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)
        self.assertEqual(newCompilerOutput, "version.cpp\n")

    def testParseIncludesNoIncludes(self):
        sample = self._readSampleFileNoIncludes()
        for stripIncludes in [True, False]:
            includesSet, newCompilerOutput = clcache.parseIncludesSet(
                sample['CompilerOutput'],
                r"C:\Projects\test\myproject\main.cpp",
                strip=stripIncludes)

            self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
            self.assertEqual(newCompilerOutput, "main.cpp\n")

    def testParseIncludesGerman(self):
        sample = self._readSampleFileDefault(lang="de")
        includesSet, _ = clcache.parseIncludesSet(
            sample['CompilerOutput'],
            r"C:\Projects\test\smartsqlite\src\version.cpp",
            strip=False)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\projects\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(
            r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)


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

        cls.includePaths = list(sorted(clcache.filesBeneath(cls.tempDir.name)))
        cls.manifestHash = 'ffffffffffffffffffffffffffffffff'
        cls.expectedManifestEntry = clcache.createManifestEntry(TestCreateManifestEntry.manifestHash,
                                                                TestCreateManifestEntry.includePaths)

    @classmethod
    def tearDownClass(cls):
        cls.tempDir.cleanup()

    def assertManifestEntryIsCorrect(self, entry):
        self.assertEqual(entry.includesContentHash, TestCreateManifestEntry.expectedManifestEntry.includesContentHash)
        self.assertEqual(entry.objectHash, TestCreateManifestEntry.expectedManifestEntry.objectHash)
        self.assertEqual(entry.includeFiles, TestCreateManifestEntry.expectedManifestEntry.includeFiles)

    def testIsConsistentWithSameInput(self):
        entry = clcache.createManifestEntry(TestCreateManifestEntry.manifestHash, TestCreateManifestEntry.includePaths)
        self.assertManifestEntryIsCorrect(entry)

    def testIsConsistentWithReverseList(self):
        reversedIncludePaths = list(reversed(TestCreateManifestEntry.includePaths))
        entry = clcache.createManifestEntry(TestCreateManifestEntry.manifestHash, reversedIncludePaths)
        self.assertManifestEntryIsCorrect(entry)

    def testIsConsistentWithDuplicateEntries(self):
        includePathsWithDuplicates = TestCreateManifestEntry.includePaths + TestCreateManifestEntry.includePaths
        entry = clcache.createManifestEntry(TestCreateManifestEntry.manifestHash, includePathsWithDuplicates)
        self.assertManifestEntryIsCorrect(entry)


class TestMemcacheStrategy(unittest.TestCase):
    def testSetGet(self):
        from pymemcache.test.utils import MockMemcacheClient
        from clcache.clcache import CompilerArtifacts, getStringHash

        with tempfile.TemporaryDirectory() as tempDir:
            memcache = CacheMemcacheStrategy("localhost", cacheDirectory=tempDir)
            memcache.client = MockMemcacheClient(allow_unicode_keys=True)
            key = getStringHash("hello")
            memcache.fileStrategy.lockFor = memcache.lockFor

            self.assertEqual(memcache.hasEntry(key), False)
            self.assertEqual(memcache.getEntry(key), None)

            dirName = memcache.fileStrategy.directoryForCache(key)  # XX requires in depth knowledge of FileStrategy
            os.makedirs(dirName)
            fileName = os.path.join(dirName, "object")
            with open(fileName, "wb") as f:
                f.write(b'Content')

            artifact = CompilerArtifacts(fileName, "", "")

            memcache.setEntry(key, artifact)
            self.assertEqual(memcache.hasEntry(key), True)
            self.assertEqual(memcache.getEntry(key).objectFilePath, artifact.objectFilePath)
            self.assertEqual(memcache.getEntry(key).stdout, artifact.stdout)
            self.assertEqual(memcache.getEntry(key).stderr, artifact.stderr)

            nonArtifact = CompilerArtifacts("random.txt", "stdout", "stderr")
            with self.assertRaises(FileNotFoundError):
                memcache.setEntry(key, nonArtifact)

    def testArgumentParsing(self):
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("")

        self.assertEqual(CacheMemcacheStrategy.splitHosts("localhost"), [("localhost", 11211)])
        self.assertEqual(CacheMemcacheStrategy.splitHosts("localhost:123"), [("localhost", 123)])

        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost:123:")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost:123:")

        self.assertEqual(CacheMemcacheStrategy.splitHosts("localhost.local, example.local"),
                         [("localhost.local", 11211), ("example.local", 11211)])
        self.assertEqual(CacheMemcacheStrategy.splitHosts("localhost.local:12345, example.local"),
                         [("localhost.local", 12345), ("example.local", 11211)])

        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local:123456")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local:123456, example.local")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local:12345,")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local,12345:")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local;12345:")


class TestCompression(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory
        self.testDir = tempfile.mkdtemp()

    def tearDown(self):
        # Remove the directory after the test
        shutil.rmtree(self.testDir)
        os.environ.clear()

    def assertEntrySizeIsCorrect(self, expectedSize):
        from clcache.clcache import copyOrLink

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
        from clcache.clcache import copyOrLink

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


if __name__ == '__main__':
    unittest.TestCase.longMessage = True
    unittest.main()
