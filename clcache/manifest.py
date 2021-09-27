import json
import os
from typing import List, NamedTuple

from atomicwrites import atomic_write

from . import WALK
from .cmdline import CommandLineAnalyzer
from .compiler import CompilerArtifactsRepository
from .errors import IncludeNotFoundException
from .hash import (
    HashAlgorithm,
    getFileHash,
    getCompilerHash,
    getFileHashes
)
from .lock import CacheLock
from .print import (
    printTraceStatement,
    printErrStr,
)
from .utils import (
    ensureDirectoryExists,
    normalizeBaseDir,
    childDirectories,
)


# ManifestEntry: an entry in a manifest file
# `includeFiles`: list of paths to include files, which this source file uses
# `includesContentsHash`: hash of the contents of the includeFiles
# `objectHash`: hash of the object in cache
class ManifestEntry(NamedTuple):
    includeFiles: List[str]
    includesContentHash: str
    objectHash: str


# TODO: not used
# Manifest file will have at most this number of hash lists in it. Need to avoi
# manifests grow too large.
MAX_MANIFEST_HASHES = 100

# String, by which BASE_DIR will be replaced in paths, stored in manifests.
# ? is invalid character for file name, so it seems ok
# to use it as mark for relative path.
BASEDIR_REPLACEMENT = '?'


class Manifest:
    def __init__(self, entries: List[ManifestEntry]=None):
        if entries is None:
            entries = []
        self._entries = entries.copy()

    def entries(self):
        return self._entries

    def addEntry(self, entry):
        """Adds entry at the top of the entries"""
        self._entries.insert(0, entry)

    def touchEntry(self, objectHash):
        """Moves entry in entryIndex position to the top of entries()"""
        entryIndex = next((i for i, e in enumerate(self.entries()) if e.objectHash == objectHash), 0)
        self._entries.insert(0, self._entries.pop(entryIndex))


class ManifestSection:
    def __init__(self, manifestSectionDir):
        self.manifestSectionDir = manifestSectionDir
        self.lock = CacheLock.forPath(self.manifestSectionDir)

    def manifestPath(self, manifestHash):
        return os.path.join(self.manifestSectionDir, manifestHash + ".json")

    def manifestFiles(self):
        return filesBeneath(self.manifestSectionDir)

    def setManifest(self, manifestHash: str, manifest: Manifest) -> None:
        manifestPath = self.manifestPath(manifestHash)
        printTraceStatement("Writing manifest with manifestHash = {} to {}".format(manifestHash, manifestPath))
        ensureDirectoryExists(self.manifestSectionDir)
        with atomic_write(manifestPath, overwrite=True) as outFile:
            # Converting namedtuple to JSON via OrderedDict preserves key names and keys order
            entries = [e._asdict() for e in manifest.entries()]
            jsonobject = {'entries': entries}
            json.dump(jsonobject, outFile, sort_keys=True, indent=2)

    def getManifest(self, manifestHash):
        fileName = self.manifestPath(manifestHash)
        if not os.path.exists(fileName):
            return None
        try:
            with open(fileName, 'r') as inFile:
                doc = json.load(inFile)
                return Manifest([ManifestEntry(e['includeFiles'], e['includesContentHash'], e['objectHash'])
                                 for e in doc['entries']])
        except IOError:
            return None
        except ValueError:
            printErrStr("clcache: manifest file %s was broken" % fileName)
            return None


class ManifestRepository:
    # Bump this counter whenever the current manifest file format changes.
    # E.g. changing the file format from {'oldkey': ...} to {'newkey': ...} requires
    # invalidation, such that a manifest that was stored using the old format is not
    # interpreted using the new format. Instead the old file will not be touched
    # again due to a new manifest hash and is cleaned away after some time.
    MANIFEST_FILE_FORMAT_VERSION = 6

    def __init__(self, manifestsRootDir):
        self._manifestsRootDir = manifestsRootDir

    def section(self, manifestHash):
        return ManifestSection(os.path.join(self._manifestsRootDir, manifestHash[:2]))

    def sections(self):
        return (ManifestSection(path) for path in childDirectories(self._manifestsRootDir))

    def clean(self, maxManifestsSize):
        manifestFileInfos = []
        for section in self.sections():
            for filePath in section.manifestFiles():
                try:
                    manifestFileInfos.append((os.stat(filePath), filePath))
                except OSError:
                    pass

        manifestFileInfos.sort(key=lambda t: t[0].st_atime, reverse=True)

        remainingObjectsSize = 0
        for stat, filepath in manifestFileInfos:
            if remainingObjectsSize + stat.st_size <= maxManifestsSize:
                remainingObjectsSize += stat.st_size
            else:
                os.remove(filepath)
        return remainingObjectsSize

    @staticmethod
    def getManifestHash(compilerBinary, commandLine, sourceFile):
        compilerHash = getCompilerHash(compilerBinary)

        # NOTE: We intentionally do not normalize command line to include
        # preprocessor options.  In direct mode we do not perform preprocessing
        # before cache lookup, so all parameters are important.  One of the few
        # exceptions to this rule is the /MP switch, which only defines how many
        # compiler processes are running simultaneusly.  Arguments that specify
        # the compiler where to find the source files are parsed to replace
        # ocurrences of CLCACHE_BASEDIR by a placeholder.
        arguments, inputFiles = CommandLineAnalyzer.parseArgumentsAndInputFiles(commandLine)
        collapseBasedirInCmdPath = lambda path: collapseBasedirToPlaceholder(os.path.normcase(os.path.abspath(path)))

        commandLine = []
        argumentsWithPaths = ("AI", "I", "FU")
        for k in sorted(arguments.keys()):
            if k in argumentsWithPaths:
                commandLine.extend(["/" + k + collapseBasedirInCmdPath(arg) for arg in arguments[k]])
            else:
                commandLine.extend(["/" + k + arg for arg in arguments[k]])

        commandLine.extend(collapseBasedirInCmdPath(arg) for arg in inputFiles)

        additionalData = "{}|{}|{}".format(
            compilerHash, commandLine, ManifestRepository.MANIFEST_FILE_FORMAT_VERSION)
        return getFileHash(sourceFile, additionalData)

    @staticmethod
    def getIncludesContentHashForFiles(includes):
        try:
            listOfHashes = getFileHashes(includes)
        except FileNotFoundError:
            raise IncludeNotFoundException
        return ManifestRepository.getIncludesContentHashForHashes(listOfHashes)

    @staticmethod
    def getIncludesContentHashForHashes(listOfHashes):
        return HashAlgorithm(','.join(listOfHashes).encode()).hexdigest()


def createManifestEntry(manifestHash, includePaths):
    sortedIncludePaths = sorted(set(includePaths))
    includeHashes = getFileHashes(sortedIncludePaths)

    safeIncludes = [collapseBasedirToPlaceholder(path) for path in sortedIncludePaths]
    includesContentHash = ManifestRepository.getIncludesContentHashForHashes(includeHashes)
    cachekey = CompilerArtifactsRepository.computeKeyDirect(manifestHash, includesContentHash)

    return ManifestEntry(safeIncludes, includesContentHash, cachekey)


# private


def filesBeneath(baseDir):
    for path, _, filenames in WALK(baseDir):
        for filename in filenames:
            yield os.path.join(path, filename)


def collapseBasedirToPlaceholder(path):
    baseDir = normalizeBaseDir(os.environ.get('CLCACHE_BASEDIR'))
    if baseDir is None:
        return path
    else:
        assert path == os.path.normcase(path)
        assert baseDir == os.path.normcase(baseDir)
        if path.startswith(baseDir):
            return path.replace(baseDir, BASEDIR_REPLACEMENT, 1)
        else:
            return path
