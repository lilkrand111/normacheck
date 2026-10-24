"""Formal DOCX checks for STP 01–2017 with the supplied rectorate clarification."""
from .checker import CheckError, check_document, inspect_document
from .reader import DocumentReadError
from .report import validate_report

__all__ = ["check_document", "inspect_document", "CheckError", "DocumentReadError", "validate_report"]
__version__ = "0.3.5"
