import os
import sys

from .clcache import (
    VERSION,
    Cache,
    printStatistics,
    cleanCache,
    clearCache,
    findCompilerBinary,
    resetStatistics,
    processCompileRequest,
    invokeRealCompiler,
    getStringHash
)
from .print import printTraceStatement
from .errors import (
    LogicException,
    ProfilerError
)

import argparse
import cProfile

def main():
    # These Argparse Actions are necessary because the first commandline
    # argument, the compiler executable path, is optional, and the argparse
    # class does not support conditional selection of positional arguments.
    # Therefore, these classes check the candidate path, and if it is not an
    # executable, stores it in the namespace as a special variable, and
    # the compiler argument Action then prepends it to its list of arguments
    class CommandCheckAction(argparse.Action):
        def __call__(self, parser, namespace, values, optional_string=None):
            if values and not values.lower().endswith(".exe"):
                setattr(namespace, "non_command", values)
                return
            setattr(namespace, self.dest, values)

    class RemainderSetAction(argparse.Action):
        def __call__(self, parser, namespace, values, optional_string=None):
            nonCommand = getattr(namespace, "non_command", None)
            if nonCommand:
                values.insert(0, nonCommand)
            setattr(namespace, self.dest, values)

    parser = argparse.ArgumentParser(description="clcache.py v" + VERSION)
    # Handle the clcache standalone actions, only one can be used at a time
    groupParser = parser.add_mutually_exclusive_group()
    groupParser.add_argument("-s", "--stats", dest="show_stats",
                             action="store_true",
                             help="print cache statistics")
    groupParser.add_argument("-c", "--clean", dest="clean_cache",
                             action="store_true", help="clean cache")
    groupParser.add_argument("-C", "--clear", dest="clear_cache",
                             action="store_true", help="clear cache")
    groupParser.add_argument("-z", "--reset", dest="reset_stats",
                             action="store_true",
                             help="reset cache statistics")
    groupParser.add_argument("-M", "--set-size", dest="cache_size", type=int,
                             default=None,
                             help="set maximum cache size (in bytes)")

    # This argument need to be optional, or it will be required for the status commands above
    parser.add_argument("compiler", default=None, action=CommandCheckAction,
                        nargs="?",
                        help="Optional path to compile executable. If not "
                             "present look in CLCACHE_CL environment variable "
                             "or search PATH for cl.exe.")
    parser.add_argument("compiler_args", action=RemainderSetAction,
                        nargs=argparse.REMAINDER,
                        help="Arguments to the compiler")

    options = parser.parse_args()

    cache = Cache()

    if options.show_stats:
        printStatistics(cache)
        return 0

    if options.clean_cache:
        cleanCache(cache)
        print('Cache cleaned')
        return 0

    if options.clear_cache:
        clearCache(cache)
        print('Cache cleared')
        return 0

    if options.reset_stats:
        resetStatistics(cache)
        print('Statistics reset')
        return 0

    if options.cache_size is not None:
        maxSizeValue = options.cache_size
        if maxSizeValue < 1:
            print("Max size argument must be greater than 0.", file=sys.stderr)
            return 1

        with cache.lock, cache.configuration as cfg:
            cfg.setMaximumCacheSize(maxSizeValue)
        return 0


    compiler = options.compiler or findCompilerBinary()
    if not (compiler and os.access(compiler, os.F_OK)):
        print("Failed to locate specified compiler, or cl.exe on PATH (and CLCACHE_CL is not set), aborting.")
        return 1

    printTraceStatement("Found real compiler binary at '{0!s}'".format(compiler))
    printTraceStatement("Arguments we care about: '{}'".format(sys.argv))

    if "CLCACHE_DISABLE" in os.environ:
        return invokeRealCompiler(compiler, options.compiler_args)[0]
    try:
        return processCompileRequest(cache, compiler, options.compiler_args)
    except LogicException as e:
        print(e)
        return 1



def mainWrapper():
    if 'CLCACHE_PROFILE' in os.environ:
        INVOCATION_HASH = getStringHash(','.join(sys.argv))
        CALL_SCRIPT = '''
import clcache
returnCode = clcache.__main__.main()
if returnCode != 0:
    raise clcache.__main__.ProfilerError(returnCode)
'''
        try:
            cProfile.run(CALL_SCRIPT, filename='clcache-{}.prof'.format(INVOCATION_HASH))
        except ProfilerError as e:
            sys.exit(e.returnCode)
    else:
        sys.exit(main())

if __name__ == '__main__':
    mainWrapper()
