# BrowseComp-Plus search

Two commands search a fixed collection of documents. Both print JSON.

- `bcp_search "your query"`: Perform a search on a knowledge source. Returns top-10 hits with docid, score, and snippet. The snippet contains the document's contents (may be truncated based on token limits).
- `bcp_get_document DOCID`: Retrieve a full document by its docid.
