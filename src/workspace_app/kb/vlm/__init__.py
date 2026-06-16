"""VLM (vision-language model) layer for KB ingest — issue #39.

Public surface:
  - ``IVlm`` — streaming, image-bearing completion ABC.
  - ``LitellmVlm`` — production impl via LiteLLM (Ollama / hosted).
  - ``VlmDescriber`` — image → Markdown description, the shared core
    of every vision-backed parser.
"""

from .describer import VlmDescriber
from .litellm import LitellmVlm
from .protocol import IVlm, OnChunk

__all__ = ["IVlm", "LitellmVlm", "OnChunk", "VlmDescriber"]
