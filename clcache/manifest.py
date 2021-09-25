from ctypes import windll, wintypes
from collections import namedtuple


# ManifestEntry: an entry in a manifest file
# `includeFiles`: list of paths to include files, which this source file uses
# `includesContentsHash`: hash of the contents of the includeFiles
# `objectHash`: hash of the object in cache
ManifestEntry = namedtuple('ManifestEntry', ['includeFiles', 'includesContentHash', 'objectHash'])

