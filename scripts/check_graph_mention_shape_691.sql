-- Read-only probe (#691): what shape is the stored mention layer in?
--
--   psql "$DSN" -f scripts/check_graph_mention_shape_691.sql
--
-- GraphMention is one row per (document, normalised surface) — a deck naming the
-- same tool on five slides is ONE row with occurrences=5. So the ratio that means
-- anything is mentions per DOCUMENT, not per slide, and section 1 reports the
-- document count rather than assuming it.
--
-- This reads what is STORED. It cannot say whether today's extractor still does
-- the same thing (the prompt last changed 2026-07-24, #630 P4/P7) and it cannot
-- say WHY any row exists — the passage that produced it is not on the row. For
-- that, run `scripts/check_graph_extraction_691.py`, which prints the slide
-- beside what came out of it. The two answer different questions; neither
-- replaces the other.
--
-- Only indexed_data is touched (norm_surface / norm_kind / source_doc_id), which
-- has carried those three since the layer was built — no migrate needed. Nothing
-- is written.

\pset pager off

\echo ''
\echo '=== 1. scale: rows, documents, distinct keys, mentions per document ==='
select count(*)                                                as mentions,
       count(distinct indexed_data->>'source_doc_id')          as docs,
       count(distinct indexed_data->>'norm_surface')           as distinct_keys,
       round(count(*)::numeric
             / nullif(count(distinct indexed_data->>'source_doc_id'), 0), 1) as per_doc
from graph_mention_meta
where not is_deleted;

\echo ''
\echo '=== 2. the number that discriminates: keys living in only ONE document ==='
\echo '    a real vocabulary has a shared core; extraction noise is nearly all singletons'
with per_key as (
  select indexed_data->>'norm_surface'                   as k,
         count(distinct indexed_data->>'source_doc_id')  as docs
  from graph_mention_meta where not is_deleted group by 1)
select count(*)                                                    as distinct_keys,
       count(*) filter (where docs = 1)                            as singletons,
       round(100.0 * count(*) filter (where docs = 1) / count(*), 1) as singleton_pct,
       count(*) filter (where docs >= 5)                           as shared_by_5plus
from per_key;

\echo ''
\echo '=== 3. surface length — a name is short, a phrase is not ==='
select width_bucket(length(indexed_data->>'norm_surface'), 0, 60, 6) as bucket,
       min(length(indexed_data->>'norm_surface'))                    as min_len,
       max(length(indexed_data->>'norm_surface'))                    as max_len,
       count(*)
from graph_mention_meta where not is_deleted
group by 1 order by 1;

\echo ''
\echo '=== 4. kinds the model chose (free text by design — the corpus decides) ==='
select coalesce(nullif(indexed_data->>'norm_kind', ''), '(no kind)') as kind, count(*)
from graph_mention_meta where not is_deleted
group by 1 order by 2 desc limit 20;

\echo ''
\echo '=== 5. the shared core: keys the most documents agree on ==='
\echo '    these should read like the vocabulary of the domain'
with per_key as (
  select indexed_data->>'norm_surface'                   as k,
         indexed_data->>'norm_kind'                      as kind,
         count(distinct indexed_data->>'source_doc_id')  as docs
  from graph_mention_meta where not is_deleted group by 1, 2)
select docs, kind, k from per_key order by docs desc, k limit 30;

\echo ''
\echo '=== 6. singletons: where noise lives, if there is any ==='
with per_key as (
  select indexed_data->>'norm_surface'                   as k,
         indexed_data->>'norm_kind'                      as kind,
         count(distinct indexed_data->>'source_doc_id')  as docs
  from graph_mention_meta where not is_deleted group by 1, 2)
select kind, k from per_key where docs = 1 order by random() limit 40;

\echo ''
\echo '=== 7. the heaviest single document, end to end ==='
\echo '    if one deck produced hundreds of fragments, it is visible here'
with d as (
  select indexed_data->>'source_doc_id' as doc, count(*) as n
  from graph_mention_meta where not is_deleted
  group by 1 order by 2 desc limit 1)
select d.n as mentions_in_this_doc,
       m.indexed_data->>'norm_kind'    as kind,
       m.indexed_data->>'norm_surface' as surface
from graph_mention_meta m join d on m.indexed_data->>'source_doc_id' = d.doc
where not m.is_deleted
order by 3 limit 60;
