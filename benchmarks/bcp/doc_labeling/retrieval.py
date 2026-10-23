"""A label-blind, complete-corpus SQLite FTS5 retrieval backend for the search pilot."""

import json
import re
import sqlite3
from pathlib import Path

CORPUS_REVISION = "b27b02bc3e45511b8b82a13e6f90ce761df726f6"
CORPUS_DOCUMENTS = 100195


def build_index(parquet_dir, destination):
    import pyarrow.parquet as pq

    destination = Path(destination)
    if destination.exists():
        raise ValueError("index already exists; use it or choose a new destination")
    files = sorted(Path(parquet_dir).glob("data/*.parquet"))
    if len(files) != 7:
        raise ValueError("download all seven pinned official corpus shards first")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".building.sqlite")
    if temporary.exists():
        raise ValueError(f"unfinished index retained at {temporary}; choose a new destination")
    with sqlite3.connect(temporary) as db:
        db.execute("CREATE TABLE documents(docid TEXT UNIQUE NOT NULL, text TEXT NOT NULL, url TEXT)")
        for file in files:
            for batch in pq.ParquetFile(file).iter_batches(batch_size=256):
                db.executemany("INSERT INTO documents VALUES (:docid,:text,:url)", batch.to_pylist())
            print(f"Indexed document text: {file.name}", flush=True)
        count = db.execute("SELECT count(*) FROM documents").fetchone()[0]
        if count != CORPUS_DOCUMENTS:
            raise ValueError(f"expected {CORPUS_DOCUMENTS} documents, got {count}")
        db.execute("CREATE VIRTUAL TABLE search USING fts5(text,content='documents',content_rowid='rowid',tokenize='porter unicode61')")
        db.execute("INSERT INTO search(search) VALUES ('rebuild')")
        db.execute("CREATE TABLE metadata(value TEXT)")
        db.execute("INSERT INTO metadata VALUES (?)", (json.dumps({"revision": CORPUS_REVISION,
            "documents": count, "retriever": "SQLite FTS5 BM25; OR terms; porter unicode61"}),))
    temporary.rename(destination)


class Corpus:
    def __init__(self, path, tokens):
        self.db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        self.tokens = tokens
        self.metadata = json.loads(self.db.execute("SELECT value FROM metadata").fetchone()[0])
        if self.metadata["documents"] != CORPUS_DOCUMENTS:
            raise ValueError("this experiment requires the complete corpus")

    def cut(self, text, limit):
        ids = self.tokens.tok.encode(text, add_special_tokens=False)
        return self.tokens.tok.decode(ids[:limit], skip_special_tokens=True) if len(ids) > limit else text

    def search(self, query):
        terms = list(dict.fromkeys(re.findall(r"\w+", query, re.UNICODE)))[:32]
        if not terms:
            return []
        match = " OR ".join('"' + term + '"' for term in terms)
        hits = self.db.execute("SELECT d.docid,d.url,d.text,bm25(search) FROM search "
            "JOIN documents d ON d.rowid=search.rowid WHERE search MATCH ? ORDER BY bm25(search),d.docid LIMIT 10", (match,))
        return [{"docid": docid, "url": url, "snippet": self.cut(text, 512)} for docid, url, text, _ in hits]

    def document(self, docid):
        row = self.db.execute("SELECT text FROM documents WHERE docid=?", (docid,)).fetchone()
        if row is None:
            raise ValueError("document not found")
        return self.cut(row[0], 8192)
