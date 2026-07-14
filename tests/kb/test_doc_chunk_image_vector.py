"""#513 P2 — DocChunk carries an additive, nullable image vector.

``embedding_img`` sits beside the text ``embedding`` (and ``embedding_alt``): a
new nullable Vector column so one chunk can hold both a text-description vector
and an image vector. Nullable ⇒ existing chunks (image vector unset) are
unaffected — the additive guarantee.
"""

from workspace_app.kb.image_embedder import HashImageEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, IMG_EMBED_DIM, Collection, DocChunk


def _chunk(spec, cid, **kw):
    rm = spec.get_resource_manager(DocChunk)
    rid = rm.create(DocChunk(collection_id=cid, seq=0, start=0, end=1, text="x", **kw)).resource_id
    return rm.get(rid).data


def _coll(spec):
    return spec.get_resource_manager(Collection).create(Collection(name="d")).resource_id


def test_image_vector_defaults_to_none():
    spec = make_spec(default_user="u")
    got = _chunk(spec, _coll(spec))
    assert got.embedding_img is None  # unset ⇒ existing chunks are unaffected


def test_image_vector_round_trips_beside_the_text_embedding():
    spec = make_spec(default_user="u")
    img_vec = HashImageEmbedder(dim=IMG_EMBED_DIM).embed_query_image(b"defect.png")
    text_vec = [0.0] * EMBED_DIM  # a stand-in text-description embedding
    got = _chunk(spec, _coll(spec), embedding=text_vec, embedding_img=img_vec)
    assert got.embedding_img == img_vec  # image vector round-trips
    assert got.embedding == text_vec  # and coexists with the text vector on one chunk
