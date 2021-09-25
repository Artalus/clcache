from ctypes import windll, wintypes
from collections import namedtuple
import json
import os

from atomicwrites import atomic_write

from . import WALK
from .lock import CacheLock
from .print import (
    printTraceStatement,
    printErrStr,
)
from .utils import (
    ensureDirectoryExists,
)

# ManifestEntry: an entry in a manifest file
# `includeFiles`: list of paths to include files, which this source file uses
# `includesContentsHash`: hash of the contents of the includeFiles
# `objectHash`: hash of the object in cache
ManifestEntry = namedtuple('ManifestEntry', ['includeFiles', 'includesContentHash', 'objectHash'])


class Manifest:
    def __init__(self, entries=None):
        if entries is None:
            entries = []
        self._entries = entries.copy()

    def entries(self):
        return self._entries

    def addEntry(self, entry):
        """Adds entry at the top of the entries"""
        self._entries.insert(0, entry)

    def touchEntry(self, objectHash):
        """Moves entry in entryIndex position to the top of entries()"""
        entryIndex = next((i for i, e in enumerate(self.entries()) if e.objectHash == objectHash), 0)
        self._entries.insert(0, self._entries.pop(entryIndex))


class ManifestSection:
    def __init__(self, manifestSectionDir):
        self.manifestSectionDir = manifestSectionDir
        self.lock = CacheLock.forPath(self.manifestSectionDir)

    def manifestPath(self, manifestHash):
        return os.path.join(self.manifestSectionDir, manifestHash + ".json")

    def manifestFiles(self):
        return filesBeneath(self.manifestSectionDir)

    def setManifest(self, manifestHash, manifest):
        manifestPath = self.manifestPath(manifestHash)
        printTraceStatement("Writing manifest with manifestHash = {} to {}".format(manifestHash, manifestPath))
        ensureDirectoryExists(self.manifestSectionDir)
        with atomic_write(manifestPath, overwrite=True) as outFile:
            # Converting namedtuple to JSON via OrderedDict preserves key names and keys order
            entries = [e._asdict() for e in manifest.entries()]
            jsonobject = {'entries': entries}
            json.dump(jsonobject, outFile, sort_keys=True, indent=2)

    def getManifest(self, manifestHash):
        fileName = self.manifestPath(manifestHash)
        if not os.path.exists(fileName):
            return None
        try:
            with open(fileName, 'r') as inFile:
                doc = json.load(inFile)
                return Manifest([ManifestEntry(e['includeFiles'], e['includesContentHash'], e['objectHash'])
                                 for e in doc['entries']])
        except IOError:
            return None
        except ValueError:
            printErrStr("clcache: manifest file %s was broken" % fileName)
            return None




# private


def filesBeneath(baseDir):
    for path, _, filenames in WALK(baseDir):
        for filename in filenames:
            yield os.path.join(path, filename)


