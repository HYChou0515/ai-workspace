from workspace_app.kb.embedder import HashEmbedder, LitellmEmbedder


def test_hash_embedder_is_deterministic_and_correct_dim():
    e = HashEmbedder(dim=10)  # not a multiple of the hash's 8 floats → fills mid-digest
    v1 = e.embed_documents(["hello world"])[0]
    v2 = e.embed_documents(["hello world"])[0]
    v3 = e.embed_documents(["different"])[0]
    assert e.dim == 10 and len(v1) == 10
    assert v1 == v2  # deterministic
    assert v1 != v3  # content-sensitive
    assert all(isinstance(x, float) for x in v1)


def test_query_and_doc_instruction_prefixes_are_applied():
    plain = HashEmbedder(dim=16)
    # no prefixes → query and doc embeddings of the same text match
    assert plain.embed_query("x") == plain.embed_documents(["x"])[0]

    pref = HashEmbedder(dim=16, query_prefix="Q: ", doc_prefix="D: ")
    # distinct prefixes → same text embeds differently as a query vs a document
    assert pref.embed_query("x") != pref.embed_documents(["x"])[0]
    # the query prefix is actually prepended (matches the plain embed of "Q: x")
    assert pref.embed_query("x") == plain.embed_documents(["Q: x"])[0]
    assert pref.embed_documents(["x"])[0] == plain.embed_documents(["D: x"])[0]


def test_litellm_embedder_constructs_and_reports_its_dim():
    e = LitellmEmbedder("ollama/qwen3-embedding", dim=1024, query_prefix="Q: ")
    assert isinstance(e, LitellmEmbedder)
    assert e.dim == 1024  # must match the DocChunk Vector dim (KB_EMBED_DIM)
