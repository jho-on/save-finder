"""Domain exceptions.

Every failure that belongs to SaveFinder is raised as a subclass of
:class:`SaveFinderError`, so the CLI, the pipeline and the web layer can
translate failures without catching unrelated built-ins.
"""

from __future__ import annotations


class SaveFinderError(Exception):
    """Base class for all SaveFinder domain errors."""


class ConfigError(SaveFinderError):
    """Settings or taxonomy file is missing or invalid."""


class DatabaseError(SaveFinderError):
    """Database schema or migration failure."""


class TaxonomyError(ConfigError):
    """Taxonomy file is invalid or violates the closed-taxonomy rules."""


class PlatformError(SaveFinderError):
    """Platform adapter failure."""


class UnsupportedURL(PlatformError):
    """URL does not belong to the platform or is not a supported post."""


class UnknownPlatform(PlatformError):
    """No adapter is registered for the requested platform."""


class ExportImportError(SaveFinderError):
    """Export could not be parsed or imported."""


class EnrichError(SaveFinderError):
    """Metadata or thumbnail could not be obtained."""


class DownloadError(SaveFinderError):
    """Media could not be downloaded."""


class MediaError(SaveFinderError):
    """Audio, frame or image processing failed."""


class ClassifierError(SaveFinderError):
    """Classification failed or produced an invalid result."""
