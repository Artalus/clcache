from ctypes import windll, wintypes
import hashlib
import errno
import os
import pickle
from typing import Dict

from . import VERSION

HashAlgorithm = hashlib.md5

# Define some Win32 API constants here to avoid dependency on win32pipe
NMPWAIT_WAIT_FOREVER = wintypes.DWORD(0xFFFFFFFF)
ERROR_PIPE_BUSY = 231


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


def getStringHash(dataString):
    hasher = HashAlgorithm()
    hasher.update(dataString.encode("UTF-8"))
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
