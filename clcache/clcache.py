#!/usr/bin/env python
#
# This file is part of the clcache project.
#
# The contents of this file are subject to the BSD 3-Clause License, the
# full text of which is available in the accompanying LICENSE file at the
# root directory of this project.
#
import concurrent.futures
import contextlib
import os
import re
import sys
from typing import ContextManager, List, Tuple, Iterator, Optional, Generator, TypeVar, Union, Set, Callable

from .errors import *
from .print import *
from .cmdline import *
from .cfg import Configuration
from .lock import CacheLock
from .stats import Statistics
from .utils import *
from .hash import *
from .manifest import *
from .compiler import *
from .types import EnvMap

AnyRepo = TypeVar('AnyRepo', CompilerArtifactsRepository, ManifestRepository)
AnySection = TypeVar('AnySection', CompilerArtifactsSection, ManifestSection)
StringLike = Union[str, bytes]
CompilerTuple = Tuple[int, str, str, bool]
StatisticsUpdate = Callable[[Statistics], None]

@contextlib.contextmanager
def allSectionsLocked(repository: AnyRepo) -> Generator[None, None, None]:
    sections = list(repository.sections())
    for section in sections:
        section.lock.acquire()
    try:
        yield
    finally:
        for section in sections:
            section.lock.release()

class CacheFileStrategy:
    def __init__(self, cacheDirectory: Optional[str]=None):
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

    def __str__(self) -> str:
        return "Disk cache at {}".format(self.dir)

    @property # type: ignore
    @contextlib.contextmanager
    def lock(self) -> Generator[None, None, None]:
        with allSectionsLocked(self.manifestRepository), \
             allSectionsLocked(self.compilerArtifactsRepository), \
             self.statistics.lock:
            yield

    def lockFor(self, key: str) -> CacheLock:
        assert isinstance(self.compilerArtifactsRepository.section(key).lock, CacheLock)
        return self.compilerArtifactsRepository.section(key).lock

    def manifestLockFor(self, key: str) -> CacheLock:
        return self.manifestRepository.section(key).lock

    def getEntry(self, key: str) -> CompilerArtifacts:
        return self.compilerArtifactsRepository.section(key).getEntry(key)

    def setEntry(self, key: str, value: CompilerArtifacts) -> int:
        return self.compilerArtifactsRepository.section(key).setEntry(key, value)

    def pathForObject(self, key: str) -> str:
        return self.compilerArtifactsRepository.section(key).cachedObjectName(key)

    def directoryForCache(self, key: str) -> str:
        return self.compilerArtifactsRepository.section(key).cacheEntryDir(key)

    def deserializeCacheEntry(self, key: str, objectData: bytes) -> str:
        path = self.pathForObject(key)
        ensureDirectoryExists(self.directoryForCache(key))
        with open(path, 'wb') as f:
            f.write(objectData)
        return path

    def hasEntry(self, cachekey: str) -> bool:
        return self.compilerArtifactsRepository.section(cachekey).hasEntry(cachekey)

    def setManifest(self, manifestHash: str, manifest: Manifest) -> None:
        self.manifestRepository.section(manifestHash).setManifest(manifestHash, manifest)

    def getManifest(self, manifestHash: str) -> Optional[Manifest]:
        return self.manifestRepository.section(manifestHash).getManifest(manifestHash)

    def clean(self, stats: Statistics, maximumSize: int) -> None:
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
    # TODO: make abstract class
    strategy: CacheFileStrategy
    def __init__(self, cacheDirectory: Optional[str]=None):
        if os.environ.get("CLCACHE_MEMCACHED"):
            from .storage import CacheFileWithMemcacheFallbackStrategy
            self.strategy = CacheFileWithMemcacheFallbackStrategy(os.environ.get("CLCACHE_MEMCACHED"),
                                                                  cacheDirectory=cacheDirectory)
        else:
            self.strategy = CacheFileStrategy(cacheDirectory=cacheDirectory)

    def __str__(self) -> str:
        return str(self.strategy)

    # TODO: proper lock generator type
    @property
    def lock(self) -> ContextManager[None]:
        return self.strategy.lock

    @contextlib.contextmanager
    def manifestLockFor(self, key: str) -> Generator[None, None, None]:
        with self.strategy.manifestLockFor(key):
            yield

    @property
    def configuration(self) -> Configuration:
        return self.strategy.configuration

    @property
    def statistics(self) -> Statistics:
        return self.strategy.statistics

    def clean(self, stats: Statistics, maximumSize: int) -> None:
        return self.strategy.clean(stats, maximumSize)

    @contextlib.contextmanager
    def lockFor(self, key: str) -> Generator[None, None, None]:
        with self.strategy.lockFor(key):
            yield

    def getEntry(self, key: str) -> CompilerArtifacts:
        return self.strategy.getEntry(key)

    def setEntry(self, key: str, value: CompilerArtifacts) -> int:
        return self.strategy.setEntry(key, value)

    def hasEntry(self, cachekey: str) -> bool:
        return self.strategy.hasEntry(cachekey)

    def setManifest(self, manifestHash: str, manifest: Manifest) -> None:
        self.strategy.setManifest(manifestHash, manifest)

    def getManifest(self, manifestHash: str) -> Optional[Manifest]:
        return self.strategy.getManifest(manifestHash)


def expandBasedirPlaceholder(path: str) -> str:
    baseDir = normalizeBaseDir(os.environ.get('CLCACHE_BASEDIR'))
    if path.startswith(BASEDIR_REPLACEMENT):
        if not baseDir:
            raise LogicException('No CLCACHE_BASEDIR set, but found relative path ' + path)
        return path.replace(BASEDIR_REPLACEMENT, baseDir, 1)
    else:
        return path


def printStatistics(cache: Cache) -> None:
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


def resetStatistics(cache: Cache) -> None:
    with cache.statistics.lock, cache.statistics as stats:
        stats.resetCounters()


def cleanCache(cache: Cache) -> None:
    with cache.lock, cache.statistics as stats, cache.configuration as cfg:
        cache.clean(stats, cfg.maximumCacheSize())


def clearCache(cache: Cache) -> None:
    with cache.lock, cache.statistics as stats:
        cache.clean(stats, 0)


# Returns pair:
#   1. set of include filepaths
#   2. new compiler output
# Output changes if strip is True in that case all lines with include
# directives are stripped from it
def parseIncludesSet(compilerOutput: str, sourceFile: str, strip: bool) -> Tuple[Set[str], str]:
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


def addObjectToCache(stats: Statistics, cache: Cache, cachekey: str, artifacts: CompilerArtifacts) -> bool:
    # This function asserts that the caller locked 'section' and 'stats'
    # already and also saves them
    printTraceStatement("Adding file {} to cache using key {}".format(artifacts.objectFilePath, cachekey))

    size = cache.setEntry(cachekey, artifacts)
    if size is None:
        size = os.path.getsize(artifacts.objectFilePath)
    stats.registerCacheEntry(size)

    with cache.configuration as cfg:
        return stats.currentCacheSize() >= cfg.maximumCacheSize()


def processCacheHit(cache: Cache, objectFile: str, cachekey: str) -> CompilerTuple:
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


def updateCacheStatistics(cache: Cache, method: StatisticsUpdate) -> None:
    with cache.statistics.lock, cache.statistics as stats:
        method(stats)

def printOutAndErr(out: str, err: str) -> None:
    printBinary(sys.stdout, out.encode(CL_DEFAULT_CODEC))
    printBinary(sys.stderr, err.encode(CL_DEFAULT_CODEC))

def processCompileRequest(cache: Cache, compiler: str, args: List[str]) -> int:
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
    assert isinstance(out, str)
    assert isinstance(err, str)
    printOutAndErr(out, err)
    return exitCode

def filterSourceFiles(cmdLine: List[str], sourceFiles: List[Tuple[str, str]]) -> Iterator[str]:
    setOfSources = set(sourceFile for sourceFile, _ in sourceFiles)
    skippedArgs = ('/Tc', '/Tp', '-Tp', '-Tc')
    yield from (
        arg for arg in cmdLine
        if not (arg in setOfSources or arg.startswith(skippedArgs))
    )

def scheduleJobs(cache: Cache, compiler: str, cmdLine: List[str], environment: EnvMap,
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

def processSingleSource(compiler: str, cmdLine: List[str], sourceFile: str, objectFile: str, environment: EnvMap) -> CompilerTuple:
    try:
        assert objectFile is not None
        cache = Cache()

        if 'CLCACHE_NODIRECT' in os.environ:
            return processNoDirect(cache, objectFile, compiler, cmdLine, environment)
        else:
            return processDirect(cache, objectFile, compiler, cmdLine, sourceFile)

    except IncludeNotFoundException:
        rc, o, e = invokeRealCompiler(compiler, cmdLine, environment=environment)
        assert isinstance(o, str)
        assert isinstance(e, str)
        return rc, o, e, False
    except CompilerFailedException as e:
        return e.getReturnTuple()

def processDirect(cache: Cache, objectFile: str, compiler: str, cmdLine: List[str], sourceFile: str) -> CompilerTuple:
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
    rc, o, e = invokeRealCompiler(compiler, cmdLine, captureOutput=True)
    assert isinstance(o, str)
    assert isinstance(e, str)
    compilerResult = (rc, o, e)
    if manifestHit is None:
        includePaths, compilerOutput = parseIncludesSet(o, sourceFile, stripIncludes)
        compilerResult = (rc, compilerOutput, e)

    with cache.manifestLockFor(manifestHash):
        if manifestHit is not None:
            return ensureArtifactsExist(cache, cachekey, unusableManifestMissReason,
                                        objectFile, compilerResult)

        entry = createManifestEntry(manifestHash, includePaths)
        cachekey = entry.objectHash

        def addManifest() -> None:
            manifest = cache.getManifest(manifestHash) or Manifest()
            manifest.addEntry(entry)
            cache.setManifest(manifestHash, manifest)

        return ensureArtifactsExist(cache, cachekey, unusableManifestMissReason,
                                    objectFile, compilerResult, addManifest)


def processNoDirect(cache: Cache, objectFile: str, compiler: str, cmdLine: List[str], environment: EnvMap) -> CompilerTuple:
    cachekey = CompilerArtifactsRepository.computeKeyNodirect(compiler, cmdLine, environment)
    with cache.lockFor(cachekey):
        if cache.hasEntry(cachekey):
            return processCacheHit(cache, objectFile, cachekey)

    rc, o, e = invokeRealCompiler(compiler, cmdLine, captureOutput=True, environment=environment)
    assert isinstance(o, str)
    assert isinstance(e, str)

    return ensureArtifactsExist(cache, cachekey, Statistics.registerCacheMiss,
                                objectFile, (rc, o, e))


def ensureArtifactsExist(cache: Cache,
        cachekey: str,
        reason: StatisticsUpdate,
        objectFile: str,
        compilerResult: Tuple[int, str, str],
        extraCallable: Optional[Callable[[],None]]=None) \
        -> CompilerTuple:
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
