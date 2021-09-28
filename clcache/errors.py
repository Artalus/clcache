
from typing import Tuple


class IncludeNotFoundException(Exception):
    pass


class CacheLockException(Exception):
    pass


class CompilerFailedException(Exception):
    def __init__(self, exitCode: int, msgErr: str, msgOut: str=""):
        super(CompilerFailedException, self).__init__(msgErr)
        self.exitCode = exitCode
        self.msgOut = msgOut
        self.msgErr = msgErr

    def getReturnTuple(self) -> Tuple[int, str, str, bool]:
        return self.exitCode, self.msgErr, self.msgOut, False


class LogicException(Exception):
    def __init__(self, message: str):
        super(LogicException, self).__init__(message)
        self.message = message

    def __str__(self) -> str:
        return repr(self.message)



class AnalysisError(Exception):
    pass


class NoSourceFileError(AnalysisError):
    pass


class MultipleSourceFilesComplexError(AnalysisError):
    pass


class CalledForLinkError(AnalysisError):
    pass


class CalledWithPchError(AnalysisError):
    pass


class ExternalDebugInfoError(AnalysisError):
    pass


class CalledForPreprocessingError(AnalysisError):
    pass


class InvalidArgumentError(AnalysisError):
    pass


class ProfilerError(Exception):
    def __init__(self, returnCode: int):
        self.returnCode = returnCode
