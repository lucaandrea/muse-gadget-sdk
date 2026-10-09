# MD4C 0.5.2

Vendored from https://github.com/mity/md4c/tree/release-0.5.2/src.
MIT license; see LICENSE.md and the source headers. Compiled directly into the
refined Muse UI and host simulator for CommonMark/GFM parsing.

Includes the parser (md4c.c/h) and named entity lookup (entity.c/h).
Local portability patch: initialize label_contents_line_index to zero in
md_is_link_reference_definition, for ESP-IDF 6.0.1's GCC maybe-uninitialized
diagnostic with optimization and warnings as errors.

The adapter also tracks its own output-budget failure, because a table
callback's nonzero return can be consumed before md_parse returns. On failure,
the complete original bounded reply remains readable as plain text.
