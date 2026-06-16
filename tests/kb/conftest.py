import pytest
from specstar import SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="u")
    return s


@pytest.fixture
def chunker() -> FixedTokenChunker:
    # small windows so a short fixture produces several chunks
    return FixedTokenChunker(max_tokens=3, overlap_tokens=1)


@pytest.fixture
def embedder() -> HashEmbedder:
    return HashEmbedder(dim=EMBED_DIM)  # must match the DocChunk Vector dim
