VERSION = "4.2.0-dev"

# try to use os.scandir or scandir.scandir
# fall back to os.listdir if not found
# same for scandir.walk
try:
    import scandir # pylint: disable=wrong-import-position
    WALK = scandir.walk
    LIST = scandir.scandir
except ImportError:
    import os
    WALK = os.walk
    try:
        LIST = os.scandir # type: ignore # pylint: disable=no-name-in-module
    except AttributeError:
        LIST = os.listdir
