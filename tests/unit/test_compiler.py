from contextlib import contextmanager
import os
import unittest

from clcache.compiler import CompilerArtifactsRepository


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
