#!/usr/bin/env python
#
# This file is part of the clcache project.
#
# The contents of this file are subject to the BSD 3-Clause License, the
# full text of which is available in the accompanying LICENSE file at the
# root directory of this project.
#
from collections import namedtuple
from ctypes import windll, wintypes
from shutil import copyfile, copyfileobj, rmtree, which
import codecs
import concurrent.futures
import contextlib
import errno
import gzip
import hashlib
import json
import multiprocessing
import os
import pickle
import re
import subprocess
import sys
from tempfile import TemporaryFile
from typing import Any, List, Tuple, Iterator, Dict
from atomicwrites import atomic_write

from . import (
    LIST,
    WALK,
    VERSION,
)
from .errors import *
from .print import *
from .cmdline import CommandLineAnalyzer
from .lock import CacheLock
from .stats import (
    Statistics,
    PersistentJSONDict,
)
from .utils import *


HashAlgorithm = hashlib.md5


# The codec that is used by clcache to store compiler STDOUR and STDERR in
# output.txt and stderr.txt.
# This codec is up to us and only used for clcache internal storage.
# For possible values see https://docs.python.org/2/library/codecs.html
CACHE_COMPILER_OUTPUT_STORAGE_CODEC = 'utf-8'

# The cl default codec
CL_DEFAULT_CODEC = 'mbcs'

# Manifest file will have at most this number of hash lists in it. Need to avoi
# manifests grow too large.
MAX_MANIFEST_HASHES = 100

# String, by which BASE_DIR will be replaced in paths, stored in manifests.
# ? is invalid character for file name, so it seems ok
# to use it as mark for relative path.
BASEDIR_REPLACEMENT = '?'

# Define some Win32 API constants here to avoid dependency on win32pipe
NMPWAIT_WAIT_FOREVER = wintypes.DWORD(0xFFFFFFFF)
ERROR_PIPE_BUSY = 231

# ManifestEntry: an entry in a manifest file
# `includeFiles`: list of paths to include files, which this source file uses
# `includesContentsHash`: hash of the contents of the includeFiles
# `objectHash`: hash of the object in cache
ManifestEntry = namedtuple('ManifestEntry', ['includeFiles', 'includesContentHash', 'objectHash'])

CompilerArtifacts = namedtuple('CompilerArtifacts', ['objectFilePath', 'stdout', 'stderr'])



def filesBeneath(baseDir):
    for path, _, filenames in WALK(baseDir):
        for filename in filenames:
            yield os.path.join(path, filename)


def normalizeBaseDir(baseDir):
    if baseDir:
        baseDir = os.path.normcase(baseDir)
        if baseDir.endswith(os.path.sep):
            baseDir = baseDir[0:-1]
        return baseDir
    else:
        # Converts empty string to None
        return None


def getCachedCompilerConsoleOutput(path):
    try:
        with open(path, 'rb') as f:
            return f.read().decode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC)
    except IOError:
        return ''

def setCachedCompilerConsoleOutput(path, output):
    with open(path, 'wb') as f:
        f.write(output.encode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC))

class Manifest:
    def __init__(self, entries=None):
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

    def setManifest(self, manifestHash, manifest):
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


@contextlib.contextmanager
def allSectionsLocked(repository):
    sections = list(repository.sections())
    for section in sections:
        section.lock.acquire()
    try:
        yield
    finally:
        for section in sections:
            section.lock.release()


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


class CompilerArtifactsSection:
    OBJECT_FILE = 'object'
    STDOUT_FILE = 'output.txt'
    STDERR_FILE = 'stderr.txt'

    def __init__(self, compilerArtifactsSectionDir):
        self.compilerArtifactsSectionDir = compilerArtifactsSectionDir
        self.lock = CacheLock.forPath(self.compilerArtifactsSectionDir)

    def cacheEntryDir(self, key):
        return os.path.join(self.compilerArtifactsSectionDir, key)

    def cacheEntries(self):
        return childDirectories(self.compilerArtifactsSectionDir, absolute=False)

    def cachedObjectName(self, key):
        return os.path.join(self.cacheEntryDir(key), CompilerArtifactsSection.OBJECT_FILE)

    def hasEntry(self, key):
        return os.path.exists(self.cacheEntryDir(key))

    def setEntry(self, key, artifacts):
        cacheEntryDir = self.cacheEntryDir(key)
        # Write new files to a temporary directory
        tempEntryDir = cacheEntryDir + '.new'
        # Remove any possible left-over in tempEntryDir from previous executions
        rmtree(tempEntryDir, ignore_errors=True)
        ensureDirectoryExists(tempEntryDir)
        if artifacts.objectFilePath is not None:
            dstFilePath = os.path.join(tempEntryDir, CompilerArtifactsSection.OBJECT_FILE)
            copyOrLink(artifacts.objectFilePath, dstFilePath, True)
            size = os.path.getsize(dstFilePath)
        setCachedCompilerConsoleOutput(os.path.join(tempEntryDir, CompilerArtifactsSection.STDOUT_FILE),
                                       artifacts.stdout)
        if artifacts.stderr != '':
            setCachedCompilerConsoleOutput(os.path.join(tempEntryDir, CompilerArtifactsSection.STDERR_FILE),
                                           artifacts.stderr)
        # Replace the full cache entry atomically
        os.replace(tempEntryDir, cacheEntryDir)
        return size

    def getEntry(self, key):
        assert self.hasEntry(key)
        cacheEntryDir = self.cacheEntryDir(key)
        return CompilerArtifacts(
            os.path.join(cacheEntryDir, CompilerArtifactsSection.OBJECT_FILE),
            getCachedCompilerConsoleOutput(os.path.join(cacheEntryDir, CompilerArtifactsSection.STDOUT_FILE)),
            getCachedCompilerConsoleOutput(os.path.join(cacheEntryDir, CompilerArtifactsSection.STDERR_FILE))
            )


class CompilerArtifactsRepository:
    def __init__(self, compilerArtifactsRootDir):
        self._compilerArtifactsRootDir = compilerArtifactsRootDir

    def section(self, key):
        return CompilerArtifactsSection(os.path.join(self._compilerArtifactsRootDir, key[:2]))

    def sections(self):
        return (CompilerArtifactsSection(path) for path in childDirectories(self._compilerArtifactsRootDir))

    def removeEntry(self, keyToBeRemoved):
        compilerArtifactsDir = self.section(keyToBeRemoved).cacheEntryDir(keyToBeRemoved)
        rmtree(compilerArtifactsDir, ignore_errors=True)

    def clean(self, maxCompilerArtifactsSize):
        objectInfos = []
        for section in self.sections():
            for cachekey in section.cacheEntries():
                try:
                    objectStat = os.stat(section.cachedObjectName(cachekey))
                    objectInfos.append((objectStat, cachekey))
                except OSError:
                    pass

        objectInfos.sort(key=lambda t: t[0].st_atime)

        # compute real current size to fix up the stored cacheSize
        currentSizeObjects = sum(x[0].st_size for x in objectInfos)

        removedItems = 0
        for stat, cachekey in objectInfos:
            self.removeEntry(cachekey)
            removedItems += 1
            currentSizeObjects -= stat.st_size
            if currentSizeObjects < maxCompilerArtifactsSize:
                break

        return len(objectInfos)-removedItems, currentSizeObjects

    @staticmethod
    def computeKeyDirect(manifestHash, includesContentHash):
        # We must take into account manifestHash to avoid
        # collisions when different source files use the same
        # set of includes.
        return getStringHash(manifestHash + includesContentHash)

    @staticmethod
    def computeKeyNodirect(compilerBinary, commandLine, environment):
        ppcmd = ["/EP"] + [arg for arg in commandLine if arg not in ("-c", "/c")]

        returnCode, preprocessedSourceCode, ppStderrBinary = \
            invokeRealCompiler(compilerBinary, ppcmd, captureOutput=True, outputAsString=False, environment=environment)

        if returnCode != 0:
            errMsg = ppStderrBinary.decode(CL_DEFAULT_CODEC) + "\nclcache: preprocessor failed"
            raise CompilerFailedException(returnCode, errMsg)

        compilerHash = getCompilerHash(compilerBinary)
        normalizedCmdLine = CompilerArtifactsRepository._normalizedCommandLine(commandLine)

        h = HashAlgorithm()
        h.update(compilerHash.encode("UTF-8"))
        h.update(' '.join(normalizedCmdLine).encode("UTF-8"))
        h.update(preprocessedSourceCode)
        return h.hexdigest()

    @staticmethod
    def _normalizedCommandLine(cmdline):
        # Remove all arguments from the command line which only influence the
        # preprocessor; the preprocessor's output is already included into the
        # hash sum so we don't have to care about these switches in the
        # command line as well.
        argsToStrip = ("AI", "C", "E", "P", "FI", "u", "X",
                       "FU", "D", "EP", "Fx", "U", "I")

        # Also remove the switch for specifying the output file name; we don't
        # want two invocations which are identical except for the output file
        # name to be treated differently.
        argsToStrip += ("Fo",)

        # Also strip the switch for specifying the number of parallel compiler
        # processes to use (when specifying multiple source files on the
        # command line).
        argsToStrip += ("MP",)

        return [arg for arg in cmdline
                if not (arg[0] in "/-" and arg[1:].startswith(argsToStrip))]

class CacheFileStrategy:
    def __init__(self, cacheDirectory=None):
        self.dir = cacheDirectory
        if not self.dir:
            try:
                self.dir = os.environ["CLCACHE_DIR"]
            except KeyError:
                self.dir = os.path.join(os.path.expanduser("~"), "clcache")

        manifestsRootDir = os.path.join(self.dir, "manifests")
        ensureDirectoryExists(manifestsRootDir)
        self.manifestRepository = ManifestRepository(manifestsRootDir)

        compilerArtifactsRootDir = os.path.join(self.dir, "objects")
        ensureDirectoryExists(compilerArtifactsRootDir)
        self.compilerArtifactsRepository = CompilerArtifactsRepository(compilerArtifactsRootDir)

        self.configuration = Configuration(os.path.join(self.dir, "config.txt"))
        self.statistics = Statistics(os.path.join(self.dir, "stats.txt"))

    def __str__(self):
        return "Disk cache at {}".format(self.dir)

    @property # type: ignore
    @contextlib.contextmanager
    def lock(self):
        with allSectionsLocked(self.manifestRepository), \
             allSectionsLocked(self.compilerArtifactsRepository), \
             self.statistics.lock:
            yield

    def lockFor(self, key):
        assert isinstance(self.compilerArtifactsRepository.section(key).lock, CacheLock)
        return self.compilerArtifactsRepository.section(key).lock

    def manifestLockFor(self, key):
        return self.manifestRepository.section(key).lock

    def getEntry(self, key):
        return self.compilerArtifactsRepository.section(key).getEntry(key)

    def setEntry(self, key, value):
        return self.compilerArtifactsRepository.section(key).setEntry(key, value)

    def pathForObject(self, key):
        return self.compilerArtifactsRepository.section(key).cachedObjectName(key)

    def directoryForCache(self, key):
        return self.compilerArtifactsRepository.section(key).cacheEntryDir(key)

    def deserializeCacheEntry(self, key, objectData):
        path = self.pathForObject(key)
        ensureDirectoryExists(self.directoryForCache(key))
        with open(path, 'wb') as f:
            f.write(objectData)
        return path

    def hasEntry(self, cachekey):
        return self.compilerArtifactsRepository.section(cachekey).hasEntry(cachekey)

    def setManifest(self, manifestHash, manifest):
        self.manifestRepository.section(manifestHash).setManifest(manifestHash, manifest)

    def getManifest(self, manifestHash):
        return self.manifestRepository.section(manifestHash).getManifest(manifestHash)

    def clean(self, stats, maximumSize):
        currentSize = stats.currentCacheSize()
        if currentSize < maximumSize:
            return

        # Free at least 10% to avoid cleaning up too often which
        # is a big performance hit with large caches.
        effectiveMaximumSizeOverall = maximumSize * 0.9

        # Split limit in manifests (10 %) and objects (90 %)
        effectiveMaximumSizeManifests = effectiveMaximumSizeOverall * 0.1
        effectiveMaximumSizeObjects = effectiveMaximumSizeOverall - effectiveMaximumSizeManifests

        # Clean manifests
        currentSizeManifests = self.manifestRepository.clean(effectiveMaximumSizeManifests)

        # Clean artifacts
        currentCompilerArtifactsCount, currentCompilerArtifactsSize = self.compilerArtifactsRepository.clean(
            effectiveMaximumSizeObjects)

        stats.setCacheSize(currentCompilerArtifactsSize + currentSizeManifests)
        stats.setNumCacheEntries(currentCompilerArtifactsCount)


class Cache:
    def __init__(self, cacheDirectory=None):
        if os.environ.get("CLCACHE_MEMCACHED"):
            from .storage import CacheFileWithMemcacheFallbackStrategy
            self.strategy = CacheFileWithMemcacheFallbackStrategy(os.environ.get("CLCACHE_MEMCACHED"),
                                                                  cacheDirectory=cacheDirectory)
        else:
            self.strategy = CacheFileStrategy(cacheDirectory=cacheDirectory)

    def __str__(self):
        return str(self.strategy)

    @property
    def lock(self):
        return self.strategy.lock

    @contextlib.contextmanager
    def manifestLockFor(self, key):
        with self.strategy.manifestLockFor(key):
            yield

    @property
    def configuration(self):
        return self.strategy.configuration

    @property
    def statistics(self):
        return self.strategy.statistics

    def clean(self, stats, maximumSize):
        return self.strategy.clean(stats, maximumSize)

    @contextlib.contextmanager
    def lockFor(self, key):
        with self.strategy.lockFor(key):
            yield

    def getEntry(self, key):
        return self.strategy.getEntry(key)

    def setEntry(self, key, value):
        return self.strategy.setEntry(key, value)

    def hasEntry(self, cachekey):
        return self.strategy.hasEntry(cachekey)

    def setManifest(self, manifestHash, manifest):
        self.strategy.setManifest(manifestHash, manifest)

    def getManifest(self, manifestHash):
        return self.strategy.getManifest(manifestHash)


class Configuration:
    _defaultValues = {"MaximumCacheSize": 1073741824} # 1 GiB

    def __init__(self, configurationFile):
        self._configurationFile = configurationFile
        self._cfg = None

    def __enter__(self):
        self._cfg = PersistentJSONDict(self._configurationFile)
        for setting, defaultValue in self._defaultValues.items():
            if setting not in self._cfg:
                self._cfg[setting] = defaultValue
        return self

    def __exit__(self, typ, value, traceback):
        # Does not write to disc when unchanged
        self._cfg.save()

    def maximumCacheSize(self):
        return self._cfg["MaximumCacheSize"]

    def setMaximumCacheSize(self, size):
        self._cfg["MaximumCacheSize"] = size


def getCompilerHash(compilerBinary):
    stat = os.stat(compilerBinary)
    data = '|'.join([
        str(stat.st_mtime),
        str(stat.st_size),
        VERSION,
        ])
    hasher = HashAlgorithm()
    hasher.update(data.encode("UTF-8"))
    return hasher.hexdigest()


def getFileHashes(filePaths):
    if 'CLCACHE_SERVER' in os.environ:
        pipeName = r'\\.\pipe\clcache_srv_{}'.format(os.environ.get('CLCACHE_SERVER'))
        while True:
            try:
                with open(pipeName, 'w+b') as f:
                    f.write('\n'.join(filePaths).encode('utf-8'))
                    f.write(b'\x00')
                    response = f.read()
                    if response.startswith(b'!'):
                        raise pickle.loads(response[1:-1])
                    return response[:-1].decode('utf-8').splitlines()
            except OSError as e:
                if e.errno == errno.EINVAL and windll.kernel32.GetLastError() == ERROR_PIPE_BUSY:
                    windll.kernel32.WaitNamedPipeW(pipeName, NMPWAIT_WAIT_FOREVER)
                else:
                    raise
    else:
        return [getFileHashCached(filePath) for filePath in filePaths]

knownHashes: Dict[str, str] = dict()
def getFileHashCached(filePath):
    if filePath in knownHashes:
        return knownHashes[filePath]
    c = getFileHash(filePath)
    knownHashes[filePath] = c
    return c
def getFileHash(filePath, additionalData=None):
    hasher = HashAlgorithm()
    with open(filePath, 'rb') as inFile:
        hasher.update(inFile.read())
    if additionalData is not None:
        # Encoding of this additional data does not really matter
        # as long as we keep it fixed, otherwise hashes change.
        # The string should fit into ASCII, so UTF8 should not change anything
        hasher.update(additionalData.encode("UTF-8"))
    return hasher.hexdigest()


def getStringHash(dataString):
    hasher = HashAlgorithm()
    hasher.update(dataString.encode("UTF-8"))
    return hasher.hexdigest()


def expandBasedirPlaceholder(path):
    baseDir = normalizeBaseDir(os.environ.get('CLCACHE_BASEDIR'))
    if path.startswith(BASEDIR_REPLACEMENT):
        if not baseDir:
            raise LogicException('No CLCACHE_BASEDIR set, but found relative path ' + path)
        return path.replace(BASEDIR_REPLACEMENT, baseDir, 1)
    else:
        return path


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




def copyOrLink(srcFilePath, dstFilePath, writeCache=False):
    ensureDirectoryExists(os.path.dirname(os.path.abspath(dstFilePath)))

    if "CLCACHE_HARDLINK" in os.environ:
        ret = windll.kernel32.CreateHardLinkW(str(dstFilePath), str(srcFilePath), None)
        if ret != 0:
            # Touch the time stamp of the new link so that the build system
            # doesn't confused by a potentially old time on the file. The
            # hard link gets the same timestamp as the cached file.
            # Note that touching the time stamp of the link also touches
            # the time stamp on the cache (and hence on all over hard
            # links). This shouldn't be a problem though.
            os.utime(dstFilePath, None)
            return

    # If hardlinking fails for some reason (or it's not enabled), just
    # fall back to moving bytes around. Always to a temporary path first to
    # lower the chances of corrupting it.
    tempDst = dstFilePath + '.tmp'

    if "CLCACHE_COMPRESS" in os.environ:
        if "CLCACHE_COMPRESSLEVEL" in os.environ:
            compress = int(os.environ["CLCACHE_COMPRESSLEVEL"])
        else:
            compress = 6

        if writeCache is True:
            with open(srcFilePath, 'rb') as fileIn, gzip.open(tempDst, 'wb', compress) as fileOut:
                copyfileobj(fileIn, fileOut)
        else:
            with gzip.open(srcFilePath, 'rb', compress) as fileIn, open(tempDst, 'wb') as fileOut:
                copyfileobj(fileIn, fileOut)
    else:
        copyfile(srcFilePath, tempDst)
    os.replace(tempDst, dstFilePath)


def myExecutablePath():
    assert hasattr(sys, "frozen"), "is not frozen by py2exe"
    return sys.executable.upper()


def findCompilerBinary():
    if "CLCACHE_CL" in os.environ:
        path = os.environ["CLCACHE_CL"]
        if os.path.basename(path) == path:
            path = which(path)

        return path if os.path.exists(path) else None

    frozenByPy2Exe = hasattr(sys, "frozen")

    for p in os.environ["PATH"].split(os.pathsep):
        path = os.path.join(p, "cl.exe")
        if os.path.exists(path):
            if not frozenByPy2Exe:
                return path

            # Guard against recursively calling ourselves
            if path.upper() != myExecutablePath():
                return path
    return None


class CommandLineTokenizer:
    def __init__(self, content):
        self.argv = []
        self._content = content
        self._pos = 0
        self._token = ''
        self._parser = self._initialState

        while self._pos < len(self._content):
            self._parser = self._parser(self._content[self._pos])
            self._pos += 1

        if self._token:
            self.argv.append(self._token)

    def _initialState(self, currentChar):
        if currentChar.isspace():
            return self._initialState

        if currentChar == '"':
            return self._quotedState

        if currentChar == '\\':
            self._parseBackslash()
            return self._unquotedState

        self._token += currentChar
        return self._unquotedState

    def _unquotedState(self, currentChar):
        if currentChar.isspace():
            self.argv.append(self._token)
            self._token = ''
            return self._initialState

        if currentChar == '"':
            return self._quotedState

        if currentChar == '\\':
            self._parseBackslash()
            return self._unquotedState

        self._token += currentChar
        return self._unquotedState

    def _quotedState(self, currentChar):
        if currentChar == '"':
            return self._unquotedState

        if currentChar == '\\':
            self._parseBackslash()
            return self._quotedState

        self._token += currentChar
        return self._quotedState

    def _parseBackslash(self):
        numBackslashes = 0
        while self._pos < len(self._content) and self._content[self._pos] == '\\':
            self._pos += 1
            numBackslashes += 1

        followedByDoubleQuote = self._pos < len(self._content) and self._content[self._pos] == '"'
        if followedByDoubleQuote:
            self._token += '\\' * (numBackslashes // 2)
            if numBackslashes % 2 == 0:
                self._pos -= 1
            else:
                self._token += '"'
        else:
            self._token += '\\' * numBackslashes
            self._pos -= 1


def splitCommandsFile(content):
    return CommandLineTokenizer(content).argv


def expandCommandLine(cmdline):
    ret = []

    for arg in cmdline:
        if arg[0] == '@':
            includeFile = arg[1:]
            with open(includeFile, 'rb') as f:
                rawBytes = f.read()

            encoding = None

            bomToEncoding = {
                codecs.BOM_UTF32_BE: 'utf-32-be',
                codecs.BOM_UTF32_LE: 'utf-32-le',
                codecs.BOM_UTF16_BE: 'utf-16-be',
                codecs.BOM_UTF16_LE: 'utf-16-le',
            }

            for bom, enc in bomToEncoding.items():
                if rawBytes.startswith(bom):
                    encoding = enc
                    rawBytes = rawBytes[len(bom):]
                    break

            if encoding:
                includeFileContents = rawBytes.decode(encoding)
            else:
                includeFileContents = rawBytes.decode("UTF-8")

            ret.extend(expandCommandLine(splitCommandsFile(includeFileContents.strip())))
        else:
            ret.append(arg)

    return ret


def extendCommandLineFromEnvironment(cmdLine, environment):
    remainingEnvironment = environment.copy()

    prependCmdLineString = remainingEnvironment.pop('CL', None)
    if prependCmdLineString is not None:
        cmdLine = splitCommandsFile(prependCmdLineString.strip()) + cmdLine

    appendCmdLineString = remainingEnvironment.pop('_CL_', None)
    if appendCmdLineString is not None:
        cmdLine = cmdLine + splitCommandsFile(appendCmdLineString.strip())

    return cmdLine, remainingEnvironment


def invokeRealCompiler(compilerBinary, cmdLine, captureOutput=False, outputAsString=True, environment=None):
    realCmdline = [compilerBinary] + cmdLine
    printTraceStatement("Invoking real compiler as {}".format(realCmdline))

    environment = environment or os.environ

    # Environment variable set by the Visual Studio IDE to make cl.exe write
    # Unicode output to named pipes instead of stdout. Unset it to make sure
    # we can catch stdout output.
    environment.pop("VS_UNICODE_OUTPUT", None)

    returnCode = None
    stdout = b''
    stderr = b''
    if captureOutput:
        # Don't use subprocess.communicate() here, it's slow due to internal
        # threading.
        with TemporaryFile() as stdoutFile, TemporaryFile() as stderrFile:
            compilerProcess = subprocess.Popen(realCmdline, stdout=stdoutFile, stderr=stderrFile, env=environment)
            returnCode = compilerProcess.wait()
            stdoutFile.seek(0)
            stdout = stdoutFile.read()
            stderrFile.seek(0)
            stderr = stderrFile.read()
    else:
        returnCode = subprocess.call(realCmdline, env=environment)

    printTraceStatement("Real compiler returned code {0:d}".format(returnCode))

    if outputAsString:
        stdoutString = stdout.decode(CL_DEFAULT_CODEC)
        stderrString = stderr.decode(CL_DEFAULT_CODEC)
        return returnCode, stdoutString, stderrString

    return returnCode, stdout, stderr

# Returns the amount of jobs which should be run in parallel when
# invoked in batch mode as determined by the /MP argument
def jobCount(cmdLine):
    mpSwitches = [arg for arg in cmdLine if re.match(r'^/MP(\d+)?$', arg)]
    if not mpSwitches:
        return 1

    # the last instance of /MP takes precedence
    mpSwitch = mpSwitches.pop()

    count = mpSwitch[3:]
    if count != "":
        return int(count)

    # /MP, but no count specified; use CPU count
    try:
        return multiprocessing.cpu_count()
    except NotImplementedError:
        # not expected to happen
        return 2

def printStatistics(cache):
    template = """
clcache statistics:
  current cache dir         : {}
  cache size                : {:,} bytes
  maximum cache size        : {:,} bytes
  cache entries             : {}
  cache hits                : {}
  cache misses
    total                      : {}
    evicted                    : {}
    header changed             : {}
    source changed             : {}
  passed to real compiler
    called w/ invalid argument : {}
    called for preprocessing   : {}
    called for linking         : {}
    called for external debug  : {}
    called w/o source          : {}
    called w/ multiple sources : {}
    called w/ PCH              : {}""".strip()

    with cache.statistics.lock, cache.statistics as stats, cache.configuration as cfg:
        print(template.format(
            str(cache),
            stats.currentCacheSize(),
            cfg.maximumCacheSize(),
            stats.numCacheEntries(),
            stats.numCacheHits(),
            stats.numCacheMisses(),
            stats.numEvictedMisses(),
            stats.numHeaderChangedMisses(),
            stats.numSourceChangedMisses(),
            stats.numCallsWithInvalidArgument(),
            stats.numCallsForPreprocessing(),
            stats.numCallsForLinking(),
            stats.numCallsForExternalDebugInfo(),
            stats.numCallsWithoutSourceFile(),
            stats.numCallsWithMultipleSourceFiles(),
            stats.numCallsWithPch(),
        ))


def resetStatistics(cache):
    with cache.statistics.lock, cache.statistics as stats:
        stats.resetCounters()


def cleanCache(cache):
    with cache.lock, cache.statistics as stats, cache.configuration as cfg:
        cache.clean(stats, cfg.maximumCacheSize())


def clearCache(cache):
    with cache.lock, cache.statistics as stats:
        cache.clean(stats, 0)


# Returns pair:
#   1. set of include filepaths
#   2. new compiler output
# Output changes if strip is True in that case all lines with include
# directives are stripped from it
def parseIncludesSet(compilerOutput, sourceFile, strip):
    newOutput = []
    includesSet = set()

    # Example lines
    # Note: including file:         C:\Program Files (x86)\Microsoft Visual Studio 12.0\VC\INCLUDE\limits.h
    # Hinweis: Einlesen der Datei:   C:\Program Files (x86)\Microsoft Visual Studio 12.0\VC\INCLUDE\iterator
    #
    # So we match
    # - one word (translation of "note")
    # - colon
    # - space
    # - a phrase containing characters and spaces (translation of "including file")
    # - colon
    # - one or more spaces
    # - the file path, starting with a non-whitespace character
    reFilePath = re.compile(r'^(\w+): ([ \w]+):( +)(?P<file_path>\S.*)$')

    absSourceFile = os.path.normcase(os.path.abspath(sourceFile))
    for line in compilerOutput.splitlines(True):
        match = reFilePath.match(line.rstrip('\r\n'))
        if match is not None:
            filePath = match.group('file_path')
            filePath = os.path.normcase(os.path.abspath(filePath))
            if filePath != absSourceFile:
                includesSet.add(filePath)
        elif strip:
            newOutput.append(line)
    if strip:
        return includesSet, ''.join(newOutput)
    else:
        return includesSet, compilerOutput


def addObjectToCache(stats, cache, cachekey, artifacts):
    # This function asserts that the caller locked 'section' and 'stats'
    # already and also saves them
    printTraceStatement("Adding file {} to cache using key {}".format(artifacts.objectFilePath, cachekey))

    size = cache.setEntry(cachekey, artifacts)
    if size is None:
        size = os.path.getsize(artifacts.objectFilePath)
    stats.registerCacheEntry(size)

    with cache.configuration as cfg:
        return stats.currentCacheSize() >= cfg.maximumCacheSize()


def processCacheHit(cache, objectFile, cachekey):
    printTraceStatement("Reusing cached object for key {} for object file {}".format(cachekey, objectFile))

    with cache.lockFor(cachekey):
        with cache.statistics.lock, cache.statistics as stats:
            stats.registerCacheHit()

        if os.path.exists(objectFile):
            os.remove(objectFile)

        cachedArtifacts = cache.getEntry(cachekey)
        copyOrLink(cachedArtifacts.objectFilePath, objectFile)
        printTraceStatement("Finished. Exit code 0")
        return 0, cachedArtifacts.stdout, cachedArtifacts.stderr, False


def createManifestEntry(manifestHash, includePaths):
    sortedIncludePaths = sorted(set(includePaths))
    includeHashes = getFileHashes(sortedIncludePaths)

    safeIncludes = [collapseBasedirToPlaceholder(path) for path in sortedIncludePaths]
    includesContentHash = ManifestRepository.getIncludesContentHashForHashes(includeHashes)
    cachekey = CompilerArtifactsRepository.computeKeyDirect(manifestHash, includesContentHash)

    return ManifestEntry(safeIncludes, includesContentHash, cachekey)


def updateCacheStatistics(cache, method):
    with cache.statistics.lock, cache.statistics as stats:
        method(stats)

def printOutAndErr(out, err):
    printBinary(sys.stdout, out.encode(CL_DEFAULT_CODEC))
    printBinary(sys.stderr, err.encode(CL_DEFAULT_CODEC))

def processCompileRequest(cache, compiler, args):
    printTraceStatement("Parsing given commandline '{0!s}'".format(args))

    cmdLine, environment = extendCommandLineFromEnvironment(args, os.environ)
    cmdLine = expandCommandLine(cmdLine)
    printTraceStatement("Expanded commandline '{0!s}'".format(cmdLine))

    try:
        sourceFiles, objectFiles = CommandLineAnalyzer.analyze(cmdLine)
        return scheduleJobs(cache, compiler, cmdLine, environment, sourceFiles, objectFiles)
    except InvalidArgumentError:
        printTraceStatement("Cannot cache invocation as {}: invalid argument".format(cmdLine))
        updateCacheStatistics(cache, Statistics.registerCallWithInvalidArgument)
    except NoSourceFileError:
        printTraceStatement("Cannot cache invocation as {}: no source file found".format(cmdLine))
        updateCacheStatistics(cache, Statistics.registerCallWithoutSourceFile)
    except MultipleSourceFilesComplexError:
        printTraceStatement("Cannot cache invocation as {}: multiple source files found".format(cmdLine))
        updateCacheStatistics(cache, Statistics.registerCallWithMultipleSourceFiles)
    except CalledWithPchError:
        printTraceStatement("Cannot cache invocation as {}: precompiled headers in use".format(cmdLine))
        updateCacheStatistics(cache, Statistics.registerCallWithPch)
    except CalledForLinkError:
        printTraceStatement("Cannot cache invocation as {}: called for linking".format(cmdLine))
        updateCacheStatistics(cache, Statistics.registerCallForLinking)
    except ExternalDebugInfoError:
        printTraceStatement(
            "Cannot cache invocation as {}: external debug information (/Zi) is not supported".format(cmdLine)
        )
        updateCacheStatistics(cache, Statistics.registerCallForExternalDebugInfo)
    except CalledForPreprocessingError:
        printTraceStatement("Cannot cache invocation as {}: called for preprocessing".format(cmdLine))
        updateCacheStatistics(cache, Statistics.registerCallForPreprocessing)

    exitCode, out, err = invokeRealCompiler(compiler, args)
    printOutAndErr(out, err)
    return exitCode

def filterSourceFiles(cmdLine: List[str], sourceFiles: List[Tuple[str, str]]) -> Iterator[str]:
    setOfSources = set(sourceFile for sourceFile, _ in sourceFiles)
    skippedArgs = ('/Tc', '/Tp', '-Tp', '-Tc')
    yield from (
        arg for arg in cmdLine
        if not (arg in setOfSources or arg.startswith(skippedArgs))
    )

def scheduleJobs(cache: Any, compiler: str, cmdLine: List[str], environment: Any,
                 sourceFiles: List[Tuple[str, str]], objectFiles: List[str]) -> int:
    # Filter out all source files from the command line to form baseCmdLine
    baseCmdLine = [arg for arg in filterSourceFiles(cmdLine, sourceFiles) if not arg.startswith('/MP')]

    exitCode = 0
    cleanupRequired = False
    if os.getenv('CLCACHE_SINGLEFILE'):
        assert len(sourceFiles) == 1
        assert len(objectFiles) == 1
        srcFile, srcLanguage = sourceFiles[0]
        objFile = objectFiles[0]
        jobCmdLine = baseCmdLine + [srcLanguage + srcFile]
        exitCode, out, err, doCleanup = processSingleSource(
            compiler, jobCmdLine, srcFile, objFile, environment)
        printTraceStatement("Finished. Exit code {0:d}".format(exitCode))
        cleanupRequired |= doCleanup
        printOutAndErr(out, err)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobCount(cmdLine)) as executor:
            jobs = []
            for (srcFile, srcLanguage), objFile in zip(sourceFiles, objectFiles):
                jobCmdLine = baseCmdLine + [srcLanguage + srcFile]
                jobs.append(executor.submit(
                    processSingleSource,
                    compiler, jobCmdLine, srcFile, objFile, environment))
            for future in concurrent.futures.as_completed(jobs):
                exitCode, out, err, doCleanup = future.result()
                printTraceStatement("Finished. Exit code {0:d}".format(exitCode))
                cleanupRequired |= doCleanup
                printOutAndErr(out, err)

                if exitCode != 0:
                    break

    if cleanupRequired:
        cleanCache(cache)

    return exitCode

def processSingleSource(compiler, cmdLine, sourceFile, objectFile, environment):
    try:
        assert objectFile is not None
        cache = Cache()

        if 'CLCACHE_NODIRECT' in os.environ:
            return processNoDirect(cache, objectFile, compiler, cmdLine, environment)
        else:
            return processDirect(cache, objectFile, compiler, cmdLine, sourceFile)

    except IncludeNotFoundException:
        return invokeRealCompiler(compiler, cmdLine, environment=environment), False
    except CompilerFailedException as e:
        return e.getReturnTuple()

def processDirect(cache, objectFile, compiler, cmdLine, sourceFile):
    manifestHash = ManifestRepository.getManifestHash(compiler, cmdLine, sourceFile)
    manifestHit = None
    with cache.manifestLockFor(manifestHash):
        manifest = cache.getManifest(manifestHash)
        if manifest:
            for entryIndex, entry in enumerate(manifest.entries()):
                # NOTE: command line options already included in hash for manifest name
                try:
                    includesContentHash = ManifestRepository.getIncludesContentHashForFiles(
                        [expandBasedirPlaceholder(path) for path in entry.includeFiles])

                    if entry.includesContentHash == includesContentHash:
                        cachekey = entry.objectHash
                        assert cachekey is not None
                        if entryIndex > 0:
                            # Move manifest entry to the top of the entries in the manifest
                            manifest.touchEntry(cachekey)
                            cache.setManifest(manifestHash, manifest)

                        manifestHit = True
                        with cache.lockFor(cachekey):
                            if cache.hasEntry(cachekey):
                                return processCacheHit(cache, objectFile, cachekey)

                except IncludeNotFoundException:
                    pass

            unusableManifestMissReason = Statistics.registerHeaderChangedMiss
        else:
            unusableManifestMissReason = Statistics.registerSourceChangedMiss

    if manifestHit is None:
        stripIncludes = False
        if '/showIncludes' not in cmdLine:
            cmdLine = list(cmdLine)
            cmdLine.insert(0, '/showIncludes')
            stripIncludes = True
    compilerResult = invokeRealCompiler(compiler, cmdLine, captureOutput=True)
    if manifestHit is None:
        includePaths, compilerOutput = parseIncludesSet(compilerResult[1], sourceFile, stripIncludes)
        compilerResult = (compilerResult[0], compilerOutput, compilerResult[2])

    with cache.manifestLockFor(manifestHash):
        if manifestHit is not None:
            return ensureArtifactsExist(cache, cachekey, unusableManifestMissReason,
                                        objectFile, compilerResult)

        entry = createManifestEntry(manifestHash, includePaths)
        cachekey = entry.objectHash

        def addManifest():
            manifest = cache.getManifest(manifestHash) or Manifest()
            manifest.addEntry(entry)
            cache.setManifest(manifestHash, manifest)

        return ensureArtifactsExist(cache, cachekey, unusableManifestMissReason,
                                    objectFile, compilerResult, addManifest)


def processNoDirect(cache, objectFile, compiler, cmdLine, environment):
    cachekey = CompilerArtifactsRepository.computeKeyNodirect(compiler, cmdLine, environment)
    with cache.lockFor(cachekey):
        if cache.hasEntry(cachekey):
            return processCacheHit(cache, objectFile, cachekey)

    compilerResult = invokeRealCompiler(compiler, cmdLine, captureOutput=True, environment=environment)

    return ensureArtifactsExist(cache, cachekey, Statistics.registerCacheMiss,
                                objectFile, compilerResult)


def ensureArtifactsExist(cache, cachekey, reason, objectFile, compilerResult, extraCallable=None):
    cleanupRequired = False
    returnCode, compilerOutput, compilerStderr = compilerResult
    correctCompiliation = (returnCode == 0 and os.path.exists(objectFile))
    with cache.lockFor(cachekey):
        if not cache.hasEntry(cachekey):
            with cache.statistics.lock, cache.statistics as stats:
                reason(stats)
                if correctCompiliation:
                    artifacts = CompilerArtifacts(objectFile, compilerOutput, compilerStderr)
                    cleanupRequired = addObjectToCache(stats, cache, cachekey, artifacts)
            if extraCallable and correctCompiliation:
                extraCallable()
    return returnCode, compilerOutput, compilerStderr, cleanupRequired
