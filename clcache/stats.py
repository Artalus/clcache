import json

from atomicwrites import atomic_write

from .lock import CacheLock
from .print import printErrStr


class PersistentJSONDict:
    def __init__(self, fileName):
        self._dirty = False
        self._dict = {}
        self._fileName = fileName
        try:
            with open(self._fileName, 'r') as f:
                self._dict = json.load(f)
        except IOError:
            pass
        except ValueError:
            printErrStr("clcache: persistent json file %s was broken" % fileName)

    def save(self):
        if self._dirty:
            with atomic_write(self._fileName, overwrite=True) as f:
                json.dump(self._dict, f, sort_keys=True, indent=4)

    def __setitem__(self, key, value):
        self._dict[key] = value
        self._dirty = True

    def __getitem__(self, key):
        return self._dict[key]

    def __contains__(self, key):
        return key in self._dict

    def __eq__(self, other):
        return type(self) is type(other) and self.__dict__ == other.__dict__


class Statistics:
    CALLS_WITH_INVALID_ARGUMENT = "CallsWithInvalidArgument"
    CALLS_WITHOUT_SOURCE_FILE = "CallsWithoutSourceFile"
    CALLS_WITH_MULTIPLE_SOURCE_FILES = "CallsWithMultipleSourceFiles"
    CALLS_WITH_PCH = "CallsWithPch"
    CALLS_FOR_LINKING = "CallsForLinking"
    CALLS_FOR_EXTERNAL_DEBUG_INFO = "CallsForExternalDebugInfo"
    CALLS_FOR_PREPROCESSING = "CallsForPreprocessing"
    CACHE_HITS = "CacheHits"
    CACHE_MISSES = "CacheMisses"
    EVICTED_MISSES = "EvictedMisses"
    HEADER_CHANGED_MISSES = "HeaderChangedMisses"
    SOURCE_CHANGED_MISSES = "SourceChangedMisses"
    CACHE_ENTRIES = "CacheEntries"
    CACHE_SIZE = "CacheSize"

    RESETTABLE_KEYS = {
        CALLS_WITH_INVALID_ARGUMENT,
        CALLS_WITHOUT_SOURCE_FILE,
        CALLS_WITH_MULTIPLE_SOURCE_FILES,
        CALLS_WITH_PCH,
        CALLS_FOR_LINKING,
        CALLS_FOR_EXTERNAL_DEBUG_INFO,
        CALLS_FOR_PREPROCESSING,
        CACHE_HITS,
        CACHE_MISSES,
        EVICTED_MISSES,
        HEADER_CHANGED_MISSES,
        SOURCE_CHANGED_MISSES,
    }
    NON_RESETTABLE_KEYS = {
        CACHE_ENTRIES,
        CACHE_SIZE,
    }

    def __init__(self, statsFile):
        self._statsFile = statsFile
        self._stats = None
        self.lock = CacheLock.forPath(self._statsFile)

    def __enter__(self):
        self._stats = PersistentJSONDict(self._statsFile)
        for k in Statistics.RESETTABLE_KEYS | Statistics.NON_RESETTABLE_KEYS:
            if k not in self._stats:
                self._stats[k] = 0
        return self

    def __exit__(self, typ, value, traceback):
        # Does not write to disc when unchanged
        self._stats.save()

    def __eq__(self, other):
        return type(self) is type(other) and self.__dict__ == other.__dict__

    def numCallsWithInvalidArgument(self):
        return self._stats[Statistics.CALLS_WITH_INVALID_ARGUMENT]

    def registerCallWithInvalidArgument(self):
        self._stats[Statistics.CALLS_WITH_INVALID_ARGUMENT] += 1

    def numCallsWithoutSourceFile(self):
        return self._stats[Statistics.CALLS_WITHOUT_SOURCE_FILE]

    def registerCallWithoutSourceFile(self):
        self._stats[Statistics.CALLS_WITHOUT_SOURCE_FILE] += 1

    def numCallsWithMultipleSourceFiles(self):
        return self._stats[Statistics.CALLS_WITH_MULTIPLE_SOURCE_FILES]

    def registerCallWithMultipleSourceFiles(self):
        self._stats[Statistics.CALLS_WITH_MULTIPLE_SOURCE_FILES] += 1

    def numCallsWithPch(self):
        return self._stats[Statistics.CALLS_WITH_PCH]

    def registerCallWithPch(self):
        self._stats[Statistics.CALLS_WITH_PCH] += 1

    def numCallsForLinking(self):
        return self._stats[Statistics.CALLS_FOR_LINKING]

    def registerCallForLinking(self):
        self._stats[Statistics.CALLS_FOR_LINKING] += 1

    def numCallsForExternalDebugInfo(self):
        return self._stats[Statistics.CALLS_FOR_EXTERNAL_DEBUG_INFO]

    def registerCallForExternalDebugInfo(self):
        self._stats[Statistics.CALLS_FOR_EXTERNAL_DEBUG_INFO] += 1

    def numEvictedMisses(self):
        return self._stats[Statistics.EVICTED_MISSES]

    def registerEvictedMiss(self):
        self.registerCacheMiss()
        self._stats[Statistics.EVICTED_MISSES] += 1

    def numHeaderChangedMisses(self):
        return self._stats[Statistics.HEADER_CHANGED_MISSES]

    def registerHeaderChangedMiss(self):
        self.registerCacheMiss()
        self._stats[Statistics.HEADER_CHANGED_MISSES] += 1

    def numSourceChangedMisses(self):
        return self._stats[Statistics.SOURCE_CHANGED_MISSES]

    def registerSourceChangedMiss(self):
        self.registerCacheMiss()
        self._stats[Statistics.SOURCE_CHANGED_MISSES] += 1

    def numCacheEntries(self):
        return self._stats[Statistics.CACHE_ENTRIES]

    def setNumCacheEntries(self, number):
        self._stats[Statistics.CACHE_ENTRIES] = number

    def registerCacheEntry(self, size):
        self._stats[Statistics.CACHE_ENTRIES] += 1
        self._stats[Statistics.CACHE_SIZE] += size

    def unregisterCacheEntry(self, size):
        self._stats[Statistics.CACHE_ENTRIES] -= 1
        self._stats[Statistics.CACHE_SIZE] -= size

    def currentCacheSize(self):
        return self._stats[Statistics.CACHE_SIZE]

    def setCacheSize(self, size):
        self._stats[Statistics.CACHE_SIZE] = size

    def numCacheHits(self):
        return self._stats[Statistics.CACHE_HITS]

    def registerCacheHit(self):
        self._stats[Statistics.CACHE_HITS] += 1

    def numCacheMisses(self):
        return self._stats[Statistics.CACHE_MISSES]

    def registerCacheMiss(self):
        self._stats[Statistics.CACHE_MISSES] += 1

    def numCallsForPreprocessing(self):
        return self._stats[Statistics.CALLS_FOR_PREPROCESSING]

    def registerCallForPreprocessing(self):
        self._stats[Statistics.CALLS_FOR_PREPROCESSING] += 1

    def resetCounters(self):
        for k in Statistics.RESETTABLE_KEYS:
            self._stats[k] = 0
