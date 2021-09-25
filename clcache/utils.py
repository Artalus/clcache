import os
import errno

from clcache import LIST


def ensureDirectoryExists(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise



def childDirectories(path, absolute=True):
    supportsScandir = (LIST != os.listdir) # pylint: disable=comparison-with-callable
    for entry in LIST(path):
        if supportsScandir:
            if entry.is_dir():
                yield entry.path if absolute else entry.name
        else:
            absPath = os.path.join(path, entry)
            if os.path.isdir(absPath):
                yield absPath if absolute else entry
