from ctypes import windll
import os
import errno
import gzip
from shutil import copyfile, copyfileobj
from typing import Generator, Optional


def ensureDirectoryExists(path: str) -> None:
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise



def childDirectories(path: str, absolute: bool=True) -> Generator[str, None, None]:
    for entry in os.scandir(path):
        if entry.is_dir():
            yield entry.path if absolute else entry.name


def normalizeBaseDir(baseDir: Optional[str]) -> Optional[str]:
    if baseDir:
        baseDir = os.path.normcase(baseDir)
        if baseDir.endswith(os.path.sep):
            baseDir = baseDir[0:-1]
        return baseDir
    else:
        # Converts empty string to None
        return None


def copyOrLink(srcFilePath: str, dstFilePath: str, writeCache: bool=False) -> None:
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
