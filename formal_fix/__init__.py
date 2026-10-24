"""Conservative formatting fixes; the public function saves a DOCX and returns None."""
from .service import FixError, fix_document

__all__ = ["fix_document", "FixError"]
