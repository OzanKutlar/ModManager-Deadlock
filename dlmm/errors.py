"""Exception hierarchy. Every expected, user-facing failure is a DlmmError."""


class DlmmError(Exception):
    """Base class for all expected errors (printed without a traceback)."""


class ConfigError(DlmmError):
    pass


class BackupError(DlmmError):
    pass


class GameNotFoundError(DlmmError):
    pass


class GameInfoError(DlmmError):
    pass


class ArchiveError(DlmmError):
    pass


class LinkError(DlmmError):
    pass


class GameBananaError(DlmmError):
    pass


class InstallError(DlmmError):
    pass


class ProtocolError(DlmmError):
    pass


class VpkError(DlmmError):
    pass


class VpkMergeError(DlmmError):
    pass
