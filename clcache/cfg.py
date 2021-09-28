from typing import Any, Dict, Optional, cast

from .stats import PersistentJSONDict


class Configuration:
    _defaultValues: Dict[str, Any] = {"MaximumCacheSize": 1073741824} # 1 GiB
    _cfg: Optional[PersistentJSONDict]
    def __init__(self, configurationFile: str):
        self._configurationFile = configurationFile
        self._cfg = None

    # TODO: return separate ConfigurationLocked class
    def __enter__(self) -> "Configuration":
        self._cfg = PersistentJSONDict(self._configurationFile)
        for setting, defaultValue in self._defaultValues.items():
            if setting not in self._cfg:
                self._cfg[setting] = defaultValue
        return self

    def __exit__(self, typ: Any, value: Any, traceback: Any) -> None:
        # Does not write to disc when unchanged
        assert self._cfg
        self._cfg.save()

    def maximumCacheSize(self) -> int:
        assert self._cfg
        return cast(int, self._cfg["MaximumCacheSize"])

    def setMaximumCacheSize(self, size: int) -> None:
        assert self._cfg
        self._cfg["MaximumCacheSize"] = size
