"""The BrowseComp-Plus search back end (step 13a): one local HTTP service that every arm uses, so every arm
searches the identical index with the identical settings.

Settings (the CLM paper, Appendix E: "The search tool returns the top 10 snippets of 512 tokens and the document
reader is capped at 8,192 tokens"), on BrowseComp-Plus's prebuilt BM25 index, as BrowseComp-Plus's own tools do
it (searcher/searchers/bm25_searcher.py and searcher/tools.py, MIT): pyserini's LuceneSearcher, each document's
"contents", and token counts by the Qwen/Qwen3-0.6B tokenizer.
- POST /search {"query": ...}         -> the top 10 hits, each {"docid", "score", "snippet"}: the document's first
                                         512 tokens
- POST /get_document {"docid": ...}   -> {"docid", "text"}: its first 8,192 tokens, plus "truncated": true when cut;
                                         or {"error": ...}
- GET /health
Both answer JSON text (indent 2), which the tools pass to the model unchanged.

It runs in its own environment (Java 21, pyserini; ~/econo/bcp/.venv), outside ours:
  JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home ~/econo/bcp/.venv/bin/python benchmarks/bcp/search_server.py \
      --index ~/econo/bcp/BrowseComp-Plus/indexes/bm25 [--port 8790]
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

K, SNIPPET_TOKENS, DOCUMENT_TOKENS = 10, 512, 8192
TOKENIZER = "Qwen/Qwen3-0.6B"


class Search:
    def __init__(self, index: Path):
        from pyserini.search.lucene import LuceneSearcher
        from transformers import AutoTokenizer
        self.searcher = LuceneSearcher(str(index))
        self.tok = AutoTokenizer.from_pretrained(TOKENIZER)
        self.lock = threading.Lock()          # the Lucene searcher is used by one thread at a time

    def cut(self, text: str, limit: int) -> tuple[str, bool]:
        ids = self.tok.encode(text, add_special_tokens=False)
        if len(ids) <= limit:
            return text, False
        return self.tok.decode(ids[:limit], skip_special_tokens=True), True

    def search(self, query: str) -> list[dict]:
        with self.lock:
            hits = self.searcher.search(query, K)
            docs = [(h.docid, h.score, json.loads(h.lucene_document.get("raw"))["contents"]) for h in hits]
        return [{"docid": d, "score": s, "snippet": self.cut(text, SNIPPET_TOKENS)[0]} for d, s, text in docs]

    def get_document(self, docid: str) -> dict:
        with self.lock:
            doc = self.searcher.doc(str(docid))
            text = json.loads(doc.raw())["contents"] if doc is not None else None
        if text is None:
            return {"error": f"no document with docid {docid}"}
        text, cut = self.cut(text, DOCUMENT_TOKENS)
        return {"docid": str(docid), "text": text, **({"truncated": True} if cut else {})}


def serve(search: Search, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body) -> None:
            raw = (body if isinstance(body, str) else json.dumps(body, indent=2, ensure_ascii=False)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                return self.reply(200, {"ok": True, "k": K, "snippet_tokens": SNIPPET_TOKENS,
                                        "document_tokens": DOCUMENT_TOKENS, "tokenizer": TOKENIZER})
            self.reply(404, {"error": "unknown path"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self.reply(400, {"error": "the body is not JSON"})
            if self.path == "/search":
                query = body.get("query")
                if not isinstance(query, str) or not query.strip():
                    return self.reply(400, {"error": "a non-empty query is required"})
                return self.reply(200, search.search(query))
            if self.path == "/get_document":
                docid = body.get("docid")
                if docid in (None, ""):
                    return self.reply(400, {"error": "a docid is required"})
                return self.reply(200, search.get_document(str(docid)))
            self.reply(404, {"error": "unknown path"})

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--index", required=True)
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--host", default="127.0.0.1",
                   help="address to listen on; on Linux the task containers reach the host at the docker bridge, so use "
                        "0.0.0.0 there (Docker Desktop on a Mac: the default works)")
    a = p.parse_args()
    server = serve(Search(Path(a.index).expanduser()), a.port, a.host)
    print(f"search: BM25 at {a.index}, top {K}, snippets {SNIPPET_TOKENS} and documents {DOCUMENT_TOKENS} tokens "
          f"({TOKENIZER}), on {a.host}:{a.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
