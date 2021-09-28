from collections import namedtuple
import os
from shutil import (
    rmtree,
    which,
)
import subprocess
import sys
from tempfile import TemporaryFile
from typing import Dict, Generator, List, Optional, Tuple, Union

from .errors import CompilerFailedException
from .hash import (
    getStringHash,
    HashAlgorithm,
    getCompilerHash,
)
from .lock import CacheLock
from .print import printTraceStatement
from .utils import (
    childDirectories,
    ensureDirectoryExists,
    copyOrLink,
)


# The codec that is used by clcache to store compiler STDOUR and STDERR in
# output.txt and stderr.txt.
# This codec is up to us and only used for clcache internal storage.
# For possible values see https://docs.python.org/2/library/codecs.html
CACHE_COMPILER_OUTPUT_STORAGE_CODEC = 'utf-8'

# The cl default codec
CL_DEFAULT_CODEC = 'mbcs'


CompilerArtifacts = namedtuple('CompilerArtifacts', ['objectFilePath', 'stdout', 'stderr'])


class CompilerArtifactsSection:
    OBJECT_FILE = 'object'
    STDOUT_FILE = 'output.txt'
    STDERR_FILE = 'stderr.txt'

    def __init__(self, compilerArtifactsSectionDir: str):
        self.compilerArtifactsSectionDir = compilerArtifactsSectionDir
        self.lock = CacheLock.forPath(self.compilerArtifactsSectionDir)

    def cacheEntryDir(self, key: str) -> str:
        return os.path.join(self.compilerArtifactsSectionDir, key)

    def cacheEntries(self) -> Generator[str, None, None]:
        return childDirectories(self.compilerArtifactsSectionDir, absolute=False)

    def cachedObjectName(self, key: str) -> str:
        return os.path.join(self.cacheEntryDir(key), CompilerArtifactsSection.OBJECT_FILE)

    def hasEntry(self, key: str) -> bool:
        return os.path.exists(self.cacheEntryDir(key))

    def setEntry(self, key: str, artifacts: CompilerArtifacts) -> int:
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

    def getEntry(self, key: str) -> CompilerArtifacts:
        assert self.hasEntry(key)
        cacheEntryDir = self.cacheEntryDir(key)
        return CompilerArtifacts(
            os.path.join(cacheEntryDir, CompilerArtifactsSection.OBJECT_FILE),
            getCachedCompilerConsoleOutput(os.path.join(cacheEntryDir, CompilerArtifactsSection.STDOUT_FILE)),
            getCachedCompilerConsoleOutput(os.path.join(cacheEntryDir, CompilerArtifactsSection.STDERR_FILE))
            )


class CompilerArtifactsRepository:
    def __init__(self, compilerArtifactsRootDir: str):
        self._compilerArtifactsRootDir = compilerArtifactsRootDir

    def section(self, key: str) -> CompilerArtifactsSection:
        return CompilerArtifactsSection(os.path.join(self._compilerArtifactsRootDir, key[:2]))

    def sections(self) -> Generator[CompilerArtifactsSection, None, None]:
        return (CompilerArtifactsSection(path) for path in childDirectories(self._compilerArtifactsRootDir))

    def removeEntry(self, keyToBeRemoved: str) -> None:
        compilerArtifactsDir = self.section(keyToBeRemoved).cacheEntryDir(keyToBeRemoved)
        rmtree(compilerArtifactsDir, ignore_errors=True)

    def clean(self, maxCompilerArtifactsSize: float) -> Tuple[int, int]:
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
    def computeKeyDirect(manifestHash: str, includesContentHash: str) -> str:
        # We must take into account manifestHash to avoid
        # collisions when different source files use the same
        # set of includes.
        return getStringHash(manifestHash + includesContentHash)

    @staticmethod
    def computeKeyNodirect(compilerBinary: str, commandLine: List[str], environment: Dict[str, str]) -> str:
        ppcmd = ["/EP"] + [arg for arg in commandLine if arg not in ("-c", "/c")]

        returnCode, preprocessedSourceCode, ppStderrBinary = \
            invokeRealCompiler(compilerBinary, ppcmd, captureOutput=True, outputAsString=False, environment=environment)
        assert isinstance(preprocessedSourceCode, bytes)
        assert isinstance(ppStderrBinary, bytes)

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
    def _normalizedCommandLine(cmdline: List[str]) -> List[str]:
        # Remove all arguments from the command line which only influence the
        # preprocessor; the preprocessor's output is already included into the
        # hash sum so we don't have to care about these switches in the
        # command line as well.
        argsToStrip: Tuple[str, ...] = \
            ("AI", "C", "E", "P", "FI", "u", "X", "FU", "D", "EP", "Fx", "U", "I")

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

# TODO: should use more distinct type overloads
def invokeRealCompiler(compilerBinary: str, cmdLine: List[str],
        captureOutput: bool=False, outputAsString: bool=True,
        environment: Optional[Dict[str, str]]=None) \
        -> Tuple[int, Union[str, bytes], Union[str, bytes]]:
    realCmdline = [compilerBinary] + cmdLine
    printTraceStatement("Invoking real compiler as {}".format(realCmdline))

    environment = environment or os.environ # type: ignore # TODO: Dict vs _Environ mismatch
    assert environment

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


def findCompilerBinary() -> Optional[str]:
    if "CLCACHE_CL" in os.environ:
        path = os.environ["CLCACHE_CL"]
        if os.path.basename(path) == path:
            path = which(path) or ""

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


# private


def getCachedCompilerConsoleOutput(path: str) -> str:
    try:
        with open(path, 'rb') as f:
            return f.read().decode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC)
    except IOError:
        return ''


def setCachedCompilerConsoleOutput(path: str, output: str) -> None:
    with open(path, 'wb') as f:
        f.write(output.encode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC))


def myExecutablePath() -> str:
    assert hasattr(sys, "frozen"), "is not frozen by py2exe"
    return sys.executable.upper()
