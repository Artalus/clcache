import codecs
from collections import defaultdict
import multiprocessing
import os
import re
from typing import Callable, Dict, Optional, Set, Tuple, List, Any

from .errors import (
    NoSourceFileError,
    CalledForLinkError,
    CalledForPreprocessingError,
    CalledWithPchError,
    ExternalDebugInfoError,
    InvalidArgumentError,
    MultipleSourceFilesComplexError,
)
from .print import printTraceStatement
from .types import EnvMap


def basenameWithoutExtension(path: str) -> str:
    basename = os.path.basename(path)
    return os.path.splitext(basename)[0]


class Argument:
    def __init__(self, name: str):
        self.name = name

    def __len__(self) -> int:
        return len(self.name)

    def __str__(self) -> str:
        return "/" + self.name

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        key = (type(self), self.name)
        return hash(key)


# /NAMEparameter (no space, required parameter).
class ArgumentT1(Argument):
    pass


# /NAME[parameter] (no space, optional parameter)
class ArgumentT2(Argument):
    pass


# /NAME[ ]parameter (optional space)
class ArgumentT3(Argument):
    pass


# /NAME parameter (required space)
class ArgumentT4(Argument):
    pass


class CommandLineAnalyzer:
    argumentsWithParameter: Set[Argument] = {
        # /NAMEparameter
        ArgumentT1('Ob'), ArgumentT1('Yl'), ArgumentT1('Zm'),
        # /NAME[parameter]
        ArgumentT2('doc'), ArgumentT2('FA'), ArgumentT2('FR'), ArgumentT2('Fr'),
        ArgumentT2('Gs'), ArgumentT2('MP'), ArgumentT2('Yc'), ArgumentT2('Yu'),
        ArgumentT2('Zp'), ArgumentT2('Fa'), ArgumentT2('Fd'), ArgumentT2('Fe'),
        ArgumentT2('Fi'), ArgumentT2('Fm'), ArgumentT2('Fo'), ArgumentT2('Fp'),
        ArgumentT2('Wv'),
        # /NAME[ ]parameter
        ArgumentT3('AI'), ArgumentT3('D'), ArgumentT3('Tc'), ArgumentT3('Tp'),
        ArgumentT3('FI'), ArgumentT3('U'), ArgumentT3('I'), ArgumentT3('F'),
        ArgumentT3('FU'), ArgumentT3('w1'), ArgumentT3('w2'), ArgumentT3('w3'),
        ArgumentT3('w4'), ArgumentT3('wd'), ArgumentT3('we'), ArgumentT3('wo'),
        ArgumentT3('V'),
        ArgumentT3('imsvc'),
        # /NAME parameter
        ArgumentT4("Xclang"),
    }
    argumentsWithParameterSorted = sorted(argumentsWithParameter, key=len, reverse=True)

    @staticmethod
    def _getParameterizedArgumentType(cmdLineArgument: str) -> Optional[Argument]:
        # Sort by length to handle prefixes
        for arg in CommandLineAnalyzer.argumentsWithParameterSorted:
            if cmdLineArgument.startswith(arg.name, 1):
                return arg
        return None

    @staticmethod
    def parseArgumentsAndInputFiles(cmdline: List[str]) -> Tuple[Dict[str, List[str]], List[str]]:
        arguments: Dict[str, List[str]] = defaultdict(list)
        inputFiles = []
        i = 0
        while i < len(cmdline):
            cmdLineArgument = cmdline[i]

            # Plain arguments starting with / or -
            if cmdLineArgument.startswith('/') or cmdLineArgument.startswith('-'):
                arg = CommandLineAnalyzer._getParameterizedArgumentType(cmdLineArgument)
                if arg is not None:
                    if isinstance(arg, ArgumentT1):
                        value = cmdLineArgument[len(arg) + 1:]
                        if not value:
                            raise InvalidArgumentError("Parameter for {} must not be empty".format(arg))
                    elif isinstance(arg, ArgumentT2):
                        value = cmdLineArgument[len(arg) + 1:]
                    elif isinstance(arg, ArgumentT3):
                        value = cmdLineArgument[len(arg) + 1:]
                        if not value:
                            value = cmdline[i + 1]
                            i += 1
                    elif isinstance(arg, ArgumentT4):
                        value = cmdline[i + 1]
                        i += 1
                    else:
                        raise AssertionError("Unsupported argument type.")

                    arguments[arg.name].append(value)
                else:
                    argumentName = cmdLineArgument[1:] # name not followed by parameter in this case
                    arguments[argumentName].append('')

            # Response file
            elif cmdLineArgument[0] == '@':
                raise AssertionError("No response file arguments (starting with @) must be left here.")

            # Source file arguments
            else:
                inputFiles.append(cmdLineArgument)

            i += 1

        return dict(arguments), inputFiles

    @staticmethod
    def analyze(cmdline: List[str]) -> Tuple[List[Tuple[str, str]], List[str]]:
        options, inputFiles = CommandLineAnalyzer.parseArgumentsAndInputFiles(cmdline)
        # Use an override pattern to shadow input files that have
        # already been specified in the function above
        inputDict = {inputFile: '' for inputFile in inputFiles}
        compl = False
        if 'Tp' in options:
            inputDict.update({inputFile: '/Tp' for inputFile in options['Tp']})
            compl = True
        if 'Tc' in options:
            inputDict.update({inputFile: '/Tc' for inputFile in options['Tc']})
            compl = True

        # Now collect the inputFiles into the return format
        inputList = list(inputDict.items())
        if not inputDict:
            raise NoSourceFileError()

        for opt in ['E', 'EP', 'P']:
            if opt in options:
                raise CalledForPreprocessingError()

        # Technically, it would be possible to support /Zi: we'd just need to
        # copy the generated .pdb files into/out of the cache.
        if 'Zi' in options:
            raise ExternalDebugInfoError()

        if 'Yc' in options or 'Yu' in options:
            raise CalledWithPchError()

        if 'link' in options or 'c' not in options:
            raise CalledForLinkError()

        if len(inputList) > 1 and compl:
            raise MultipleSourceFilesComplexError()

        objectFiles = None
        prefix = ''
        if 'Fo' in options and options['Fo'][0]:
            # Handle user input
            tmp = os.path.normpath(options['Fo'][0])
            if os.path.isdir(tmp):
                prefix = tmp
            elif len(inputList) == 1:
                objectFiles = [tmp]
        if objectFiles is None:
            # Generate from .c/.cpp filenames
            objectFiles = [os.path.join(prefix, basenameWithoutExtension(f)) + '.obj' for f, _ in inputList]

        printTraceStatement("Compiler source files: {}".format(inputDict))
        printTraceStatement("Compiler object file: {}".format(objectFiles))
        return inputList, objectFiles


class CommandLineTokenizer:
    # TODO: needs more strict type
    State_ = Callable[[str], Any]
    def __init__(self, content: str):
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

    def _initialState(self, currentChar: str) -> Any:
        if currentChar.isspace():
            return self._initialState

        if currentChar == '"':
            return self._quotedState

        if currentChar == '\\':
            self._parseBackslash()
            return self._unquotedState

        self._token += currentChar
        return self._unquotedState

    def _unquotedState(self, currentChar: str) -> Any:
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

    def _quotedState(self, currentChar: str) -> Any:
        if currentChar == '"':
            return self._unquotedState

        if currentChar == '\\':
            self._parseBackslash()
            return self._quotedState

        self._token += currentChar
        return self._quotedState

    def _parseBackslash(self) -> None:
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



def splitCommandsFile(content: str) -> List[str]:
    return CommandLineTokenizer(content).argv


def expandCommandLine(cmdline: List[str]) -> List[str]:
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


def extendCommandLineFromEnvironment(cmdLine: List[str], environment: EnvMap) -> Tuple[List[str], EnvMap]:
    remainingEnvironment = environment.copy()

    prependCmdLineString = remainingEnvironment.pop('CL', None)
    if prependCmdLineString is not None:
        cmdLine = splitCommandsFile(prependCmdLineString.strip()) + cmdLine

    appendCmdLineString = remainingEnvironment.pop('_CL_', None)
    if appendCmdLineString is not None:
        cmdLine = cmdLine + splitCommandsFile(appendCmdLineString.strip())

    return cmdLine, remainingEnvironment


# Returns the amount of jobs which should be run in parallel when
# invoked in batch mode as determined by the /MP argument
def jobCount(cmdLine: List[str]) -> int:
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
