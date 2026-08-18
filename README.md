# In-Browser BI

A browser-based business intelligence platform. No installed executables, no dedicated database server. SQL runs entirely client-side via DuckDB compiled to WebAssembly; charts are rendered by Apache ECharts. Data is read directly from Parquet files on Amazon S3 using HTTP range requests so only the bytes each query actually touches are transferred.

---

## Project files

| File | Purpose |
|---|---|
| `index.html` | The entire application — UI, DuckDB-Wasm initialization, SQL queries, and ECharts rendering in one self-contained file. No build step required. |
| `serve.py` | Minimal Python HTTP server that adds the two headers browsers require to unlock `SharedArrayBuffer` (needed for DuckDB-Wasm's multithreaded query mode). |
| `prompt` | Original design brief describing the platform goals. |
| `from_claude.md` | Full feasibility analysis and technical specification produced from the brief. |
| `bitcoin.md` | Spec for the first dashboard: "A day in the life of Bitcoin — April 1, 2026." |

---

## Running the app

You need Python 3 (any recent version). No other dependencies.

**Start the server:**

```bash
python3 serve.py
```

The server listens on port 8080 by default. To use a different port:

```bash
python3 serve.py 9000
```

Open your browser to `http://localhost:8080`.

**Stop the server:**

Press `Ctrl-C` in the terminal where `serve.py` is running.

### Why `serve.py` instead of opening `index.html` directly?

Browsers require two HTTP response headers before they will enable `SharedArrayBuffer`, which DuckDB-Wasm uses for parallel query execution:

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

A `file://` URL cannot send HTTP headers, so DuckDB-Wasm silently falls back to single-threaded mode. `serve.py` adds these headers on every response. For the data volumes in the current dashboards (< 1M rows) single-threaded mode is functional but noticeably slower.

---

## Architecture

```
Browser tab
  └── index.html (app shell, loaded from serve.py)
        ├── DuckDB-Wasm (loaded from jsDelivr CDN, runs in a Web Worker)
        │     └── issues HTTP range requests → Parquet files on S3
        └── Apache ECharts (loaded from jsDelivr CDN)
              └── renders query results as charts
```

All computation happens in the browser. The only external servers are:

- **`serve.py`** — serves `index.html` and static assets. Stateless; no business logic.
- **S3** — holds the Parquet files. Requires a CORS policy permitting `GET`/`HEAD` from the app's origin. No compute happens here.

The CDN libraries (DuckDB-Wasm, ECharts) are fetched from jsDelivr on first load and cached by the browser.

---

## S3 and CORS

DuckDB-Wasm fetches Parquet data via HTTPS range requests. The S3 bucket must allow browser requests from the app's origin. For the public AWS blockchain dataset used in the Bitcoin dashboard, the bucket is publicly readable but may not have CORS configured for arbitrary browser origins.

If the dashboard shows a CORS error on load, download the Parquet file locally:

```bash
aws s3 cp \
  s3://aws-public-blockchain/v1.0/btc/transactions/date=2026-04-01/part-00000-435bf6c4-b054-4f2c-905a-671d4a9b6c0a-c000.snappy.parquet \
  ./btc-2026-04-01.parquet
```

Then use the **"Select Parquet file…"** button that appears in the fallback dialog. DuckDB-Wasm reads the file via the browser File API — no server access needed.

---

## Adding a new dashboard

The app is currently a single HTML file. The pattern for each dashboard is:

1. Define the Parquet source URL (or local file path).
2. Create a DuckDB view over it:
   ```sql
   CREATE OR REPLACE VIEW my_data AS
   SELECT col1, col2, ... FROM read_parquet('<url>');
   ```
3. Write SQL queries against the view and pass results to ECharts option builders (`lineOpt`, `histOpt`, or new ones).

For a private S3 bucket, generate a short-lived presigned URL server-side and pass it to `read_parquet()` — the client query code is unchanged.

---

## Committing to GitHub

### What to commit

| Include | Exclude |
|---|---|
| `index.html` | Any downloaded `.parquet` files (large binaries) |
| `serve.py` | `.env` files or files containing AWS credentials |
| `README.md` | `__pycache__/` |
| `prompt`, `from_claude.md`, `bitcoin.md` | |

### Add a `.gitignore`

```bash
cat > .gitignore << 'EOF'
*.parquet
__pycache__/
.env
*.pyc
EOF
```

### Typical commit workflow

```bash
# Check what's staged and what's changed
git status
git diff

# Stage the files you want
git add index.html serve.py README.md .gitignore

# Review what's about to be committed
git diff --staged

# Commit
git commit -m "describe what changed and why"

# Push to GitHub
git push origin main
```

### First push to a new GitHub repository

```bash
# Create the repo on GitHub first (github.com → New repository), then:
git remote add origin https://github.com/<your-username>/in-browser-bi.git
git push -u origin main
```

---

## Technology reference

| Component | Library | CDN source |
|---|---|---|
| In-browser SQL engine | [DuckDB-Wasm](https://github.com/duckdb/duckdb-wasm) | jsDelivr (`@duckdb/duckdb-wasm@latest`) |
| Charts | [Apache ECharts 5](https://echarts.apache.org) | jsDelivr (`echarts@5.5.1`) |
| Data format | [Apache Parquet](https://parquet.apache.org) | read via DuckDB-Wasm HTTP range requests |
| Dev server | Python `http.server` | stdlib, no install needed |
