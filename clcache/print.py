import os
import sys
import threading

OUTPUT_LOCK = threading.Lock()

def printBinary(stream, rawData):
    with OUTPUT_LOCK:
        stream.buffer.write(rawData)
        stream.flush()

def printTraceStatement(msg: str) -> None:
    if "CLCACHE_LOG" in os.environ:
        scriptDir = os.path.realpath(os.path.dirname(sys.argv[0]))
        with OUTPUT_LOCK:
            print(os.path.join(scriptDir, "clcache.py") + " " + msg)


def printErrStr(message):
    with OUTPUT_LOCK:
        print(message, file=sys.stderr)
