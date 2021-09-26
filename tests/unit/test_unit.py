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


class TestConfiguration(unittest.TestCase):
    def testOpenClose(self):
        with Configuration(temporaryFileName()):
            pass

    def testDefaults(self):
        with Configuration(temporaryFileName()) as cfg:
            self.assertGreaterEqual(cfg.maximumCacheSize(), 1024) # 1KiB


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


if __name__ == '__main__':
    unittest.TestCase.longMessage = True
    unittest.main()
