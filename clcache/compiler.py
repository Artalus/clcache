from collections import namedtuple
import os
from shutil import rmtree
import subprocess
from tempfile import TemporaryFile

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


# private


def getCachedCompilerConsoleOutput(path):
    try:
        with open(path, 'rb') as f:
            return f.read().decode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC)
    except IOError:
        return ''


def setCachedCompilerConsoleOutput(path, output):
    with open(path, 'wb') as f:
        f.write(output.encode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC))
