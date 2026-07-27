# Browser-Based BI Platform — Feasibility & Technical Specification

**Status:** Feasibility confirmed. Spec below is implementation-ready for v1 (internal, single org, small datasets) with a defined phase 2 for AD/LDAP.

**Scope assumptions locked in for this spec** (confirm before build):
- End users: internal team, single organization — no multi-tenancy required in v1.
- Data scale: reports/dashboards are typically under ~100MB, fit comfortably in browser memory.
- Auth: v1 ships without AD/LDAP; phase 2 adds a dedicated auth server bridging to AD/LDAP.

---

## 1. Feasibility summary

A browser-only BI platform — no installed executable, no dedicated database server, SQL execution against Parquet-on-S3 done entirely client-side — is proven, shipping technology as of 2026, not a research bet.

| Requirement | Feasible? | Evidence |
|---|---|---|
| No dedicated executable (pure browser) | Yes | DuckDB-Wasm runs DuckDB via WebAssembly, tested across Chrome, Firefox, Safari, and Node.js.[^1] |
| Query Parquet on S3 without a DB server | Yes | DuckDB reads remote Parquet via HTTP range requests, fetching only needed bytes; DuckDB-Wasm supports the same capability.[^2] Requires the S3 bucket to have a CORS policy — DuckDB-Wasm's HTTP/S3 layer must follow browser security rules.[^3] |
| No browser extension | Yes | Everything above runs as ordinary page JS/Wasm; nothing here requires an extension. |
| Standard chart types (bar/pie/line/scatter) | Yes | Apache ECharts covers all of these natively plus candlestick, heatmap, treemap, and more.[^4] |
| Sankey | Yes | Native ECharts series type.[^5] |
| 3D plots | Yes, via extension | `echarts-gl` adds WebGL 3D types (scatter3D, bar3D, surface3D), compatible with ECharts 5.x.[^6] |
| Raincloud plots | Yes, custom build | No major charting library ships this natively; it's a well-documented custom D3 composite (density curve + jittered dot strip + boxplot).[^7] |
| Animated charts | Yes | ECharts has built-in transition animation when underlying data changes, plus a timeline component.[^8] |
| AD/LDAP-gated access to reports | Yes, via bridge server | Browsers cannot speak LDAP directly. The standard pattern is a backend identity provider that federates with AD/LDAP and exposes OIDC/SAML to the app, restricting raw LDAP to backend-to-directory queries only.[^9] This matches the "secondary auth server" in your original brief exactly. |
| Scales beyond toy datasets | Yes, well past this project's needs | UW's Interactive Data Lab built Mosaic, an academic-grade framework (IEEE VIS 2024) that processes data in-browser via DuckDB-Wasm and demonstrates order-of-magnitude performance gains over prior web visualization systems, supporting real-time exploration of billion-record datasets.[^10] |
| Production precedent | Yes | Commercial BI/analytics products Evidence and Count already use DuckDB-Wasm in production.[^11] |

**Known constraints to design around (not blockers at this scale):**

- **Memory ceiling.** WebAssembly's current architecture (`wasm32`) caps addressable linear memory at 4GB; DuckDB-Wasm is constrained by 32-bit pointers to somewhat under that.[^12] At <100MB per report this is not a concern, but it bounds how far this architecture scales before a server-side pre-aggregation tier becomes necessary.
- **No native `httpfs` extension.** DuckDB-Wasm does not literally ship the `httpfs` extension; it has a separate, largely-interchangeable built-in HTTP/S3 filesystem implementation that must obey browser CORS rules.[^3] Functionally equivalent for this project's needs, but don't assume 1:1 SQL/config parity with server-side DuckDB docs.
- **Multithreading requires extra HTTP headers.** DuckDB-Wasm's default mode is single-threaded; parallel query execution requires `SharedArrayBuffer`, which browsers only allow on cross-origin-isolated pages — i.e., the web server must send `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp`.[^13]
- **Cross-browser persistence is inconsistent.** OPFS (used for local caching of query results/files across sessions) has full support in Chrome/Edge; Firefox and Safari currently fall back to memory-only, so don't rely on OPFS caching as a hard requirement for cross-session speed.[^14]
- **Bundle size.** The DuckDB-Wasm binary is multi-megabyte (observed as large as ~33MB for the "eh" build in one field report, more typically ~2MB for the core bundle depending on which build is chosen); it caches well after first load but affects first-visit latency.[^15]

**Bottom line:** every individual requirement in the brief has a proven, citable implementation path. The one piece that needs real engineering care — beyond "wire the libraries together" — is the security model, because DuckDB-Wasm runs entirely client-side: whatever Parquet bytes reach the browser are visible to that browser's user, regardless of what SQL predicate they typed. Access control has to happen *before* data reaches the browser (at the S3/URL layer), not inside the SQL layer. Section 5 covers this in detail.

---

## 2. Architecture overview

See the diagram above. Three tiers, one of them optional in v1:

1. **Static web server** — serves the app shell: HTML, JS bundle, the DuckDB-Wasm `.wasm`/worker files, and the ECharts/D3/echarts-gl bundles. Stateless. Must send COOP/COEP headers.
2. **Browser (client tier)** — does all the work. Loads DuckDB-Wasm into a Web Worker, issues SQL against Parquet files on S3 over HTTPS range requests, gets results back as Apache Arrow, hands them to the visualization layer.
3. **S3 bucket** — holds the Parquet files. Needs a CORS policy allowing `GET`/`HEAD` from the app's origin(s). No compute happens here; it's a static, range-request-capable file store.
4. **Auth server (phase 2)** — a small backend that federates with AD/LDAP and issues OIDC tokens / scoped, time-limited S3 access to the browser. Not needed for v1 given the current scope (internal team, phase-2 AD/LDAP), but the URL-scoping and report-ACL design in Section 5 should be built with this addition in mind so v1 doesn't need to be re-architected later.

### Why this shape and not a "thin backend" shape

The alternative — a small backend API that runs DuckDB server-side and streams results to the browser — is also viable and is what Mosaic's `socket`/`rest` client modes support.[^10] It's a legitimate fallback if data sizes grow past what's comfortable in-browser (see Section 8, scaling triggers). But it reintroduces a database server to operate and scale, which is the thing this project is explicitly trying to avoid. Recommendation: build the fully client-side version first; keep the query layer abstracted (Section 4.2) so a server-side DuckDB fallback can be added later without a rewrite.

---

## 3. Component specifications

### 3.1 Static web server

**Responsibility:** serve the SPA (single-page app) shell and all static assets. No business logic, no database connection, no session state beyond what phase-2 auth requires.

**Required response headers** (site-wide or at minimum on the document that hosts DuckDB-Wasm):

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

These are required to unlock `SharedArrayBuffer` and therefore DuckDB-Wasm's multithreaded query execution; without them DuckDB-Wasm silently falls back to single-threaded mode.[^13] Note the side effect: COEP `require-corp` means every cross-origin resource the page loads (fonts, analytics scripts, etc.) must itself opt in via CORP/CORS headers, or it will be blocked.[^16] Inventory third-party scripts before enabling this in production.

**Hosting options:** any static file host works (S3+CloudFront, Nginx, Vercel/Netlify-style platforms, a container behind a CDN). The only non-negotiable requirement is the ability to set the two headers above on the served documents — this ruled out plain GitHub Pages historically, though workarounds exist via a service-worker shim if a header-configurable host isn't available.[^17]

**Recommended stack:** any static hosting + CDN in front. No specific framework requirement; a Vite or Next.js static export both work fine for the app shell.

### 3.2 Client-side query engine (DuckDB-Wasm)

**Package:** `@duckdb/duckdb-wasm` (npm).

**Initialization pattern** (runs in a Web Worker so queries don't block the UI thread):

```javascript
import * as duckdb from '@duckdb/duckdb-wasm';

const bundles = duckdb.getJsDelivrBundles(); // or self-hosted MANUAL_BUNDLES for offline/CSP-locked envs
const bundle = await duckdb.selectBundle(bundles);
const worker = new Worker(bundle.mainWorker);
const logger = new duckdb.ConsoleLogger();
const db = new duckdb.AsyncDuckDB(logger, worker);
await db.instantiate(bundle.mainModule, bundle.pthreadWorker);

const conn = await db.connect();
```

**Querying a Parquet file on S3:**

```sql
SELECT region, SUM(revenue) AS total_revenue
FROM read_parquet('https://<bucket>.s3.<region>.amazonaws.com/reports/q3-sales.parquet')
GROUP BY region
ORDER BY total_revenue DESC;
```

Because DuckDB-Wasm issues HTTP range requests, this pulls only the row groups and columns the query actually touches rather than downloading the whole file — this is what makes multi-hundred-MB-plus Parquet files usable interactively in a browser tab.[^2]

**Schema/metadata inspection without downloading data** (useful for building a "browse available datasets" UI):

```sql
SELECT * FROM parquet_schema('https://<bucket>.s3.<region>.amazonaws.com/reports/q3-sales.parquet');
```

This reads only the Parquet file footer.[^18]

**Threading:** set `maxThreads` in `DuckDBConfig` for parallel query execution — only takes effect on cross-origin-isolated pages (Section 3.1).[^13] Default without COOP/COEP is single-threaded, which is a fine starting point for v1 given the small data scale.

**Local caching (optional, progressive enhancement):** OPFS lets DuckDB-Wasm persist a local copy of frequently-queried Parquet files so repeat visits skip the network fetch. Because OPFS support is Chrome/Edge-only today,[^14] implement this as a cache that's used opportunistically when available and silently skipped otherwise — never a hard dependency for correctness.

### 3.3 Data layer (S3 + Parquet)

**File layout:** partition Parquet files by whatever the reports filter on most often (e.g., by date or business unit), following standard Hive-style partitioning (`s3://bucket/dataset/year=2026/month=07/part-0.parquet`). This lets DuckDB's predicate pushdown skip whole files/row-groups when a report filters on the partition key, reducing bytes transferred to the browser.

**Required S3 CORS configuration** (bucket-level, via the S3 console, CLI, or IaC):

```json
[
  {
    "AllowedOrigins": ["https://your-bi-app.example.com"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["Range", "Content-Type", "Authorization"],
    "ExposeHeaders": ["Content-Range", "Content-Length", "ETag", "Accept-Ranges"],
    "MaxAgeSeconds": 3600
  }
]
```

`GET`/`HEAD` are Amazon S3's supported CORS methods for this use case; range-request support depends on `Range` being an allowed request header and `Content-Range`/`Accept-Ranges` being exposed so the browser can read them back.[^19] Avoid `"AllowedOrigins": ["*"]` in production — CORS doesn't make objects public, but an open origin list means any website that a logged-in user visits could read data their browser is authorized to fetch.[^20]

**Access pattern:** for v1 (internal team, single org), the simplest workable pattern is: bucket is *not* public; the app server issues short-lived, per-report presigned S3 URLs (or a CloudFront signed URL/cookie) after checking the requesting user is entitled to that report. DuckDB-Wasm queries the presigned URL directly — this requires no changes to the client-side query code, since `read_parquet('<url>')` accepts any HTTPS URL DuckDB-Wasm can range-request against.

### 3.4 Visualization layer

**Primary library: Apache ECharts** (`echarts` npm package). Covers bar, pie, line, scatter, sankey, and a large additional catalog (treemap, sunburst, calendar heatmap, parallel coordinates, gauge, geo) out of the box, with built-in transition animation when the underlying dataset changes.[^4][^8]

**3D: `echarts-gl`** (separate npm package, WebGL-based). Adds `scatter3D`, `bar3D`, `surface3D`, and globe visualization; compatible with ECharts 5.x.[^6]

```javascript
import * as echarts from 'echarts/core';
import { Scatter3DChart } from 'echarts-gl/charts';
import { Grid3DComponent } from 'echarts-gl/components';
echarts.use([Scatter3DChart, Grid3DComponent]);
```

**Raincloud plots: custom D3 component.** No library ships this natively. Build as three layered D3 primitives sharing one x-scale: a kernel-density curve (`d3.area()`), a jittered strip of raw-value dots, and a boxplot — the standard published construction.[^7] Budget this as a dedicated, testable component (it's the one chart type that isn't "configure a library," it's "build a small library").

**Where D3 fits alongside ECharts:** use ECharts for the standard catalog (fast to build, good defaults, good performance ceiling), and reach for raw D3 only for the bespoke pieces ECharts doesn't cover — raincloud plots here, plus any other statistical/novel chart types that come up later. Don't rebuild things ECharts already does well in D3; that's duplicated maintenance for no benefit.

**Performance note:** ECharts' Canvas-by-default renderer plus `series.sampling: 'lttb'` downsampling handles well over 100k points per chart, which is far beyond what a <100MB Parquet report will realistically push through in a single chart.[^21]

### 3.5 Auth server (phase 2 — AD/LDAP bridge)

Deferred per current scope, but documenting the target shape now avoids a v1 re-architecture.

**Why it must be a backend, not browser code:** LDAP is not a protocol a browser can speak directly (no `fetch`-based LDAP client exists as a serious option, and even if one did, exposing raw directory bind credentials to client-side JS would be a serious security regression). The standard, well-established pattern is a backend identity provider that:

1. Federates with AD/LDAP as its user store (via LDAP or LDAPS on the backend side only).
2. Exposes OIDC (preferred for new applications) and/or SAML to the browser-facing app.
3. Issues short-lived tokens that the app then uses to request scoped S3 access (presigned URLs or CloudFront signed cookies) for the specific reports/datasets that user's AD group membership entitles them to.[^9]

**Build vs. buy:** for phase 2, evaluate an off-the-shelf identity broker (Keycloak is a common open-source choice; Okta/Auth0/Entra ID/AWS Cognito are common managed choices) that already speaks LDAP-to-OIDC bridging, rather than hand-rolling the LDAP bind logic. This is a well-trodden integration path, not something specific to this project.

**What changes in the client when phase 2 ships:** the app adds a login redirect to the OIDC provider and an authenticated fetch to a small "which reports can I see, and here are scoped URLs for them" endpoint before any `read_parquet()` calls. The DuckDB-Wasm query code itself is unaffected — it already accepts arbitrary presigned URLs (Section 3.3).

---

## 4. Data flow & query lifecycle

1. Browser loads the app shell from the static web server (HTML/JS/Wasm), with COOP/COEP headers already set.
2. App initializes DuckDB-Wasm in a Web Worker (Section 3.2).
3. *(Phase 2 only)* App checks for a valid session; if none, redirects to the auth server's OIDC login, which federates to AD/LDAP.
4. App fetches the list of reports the user may see, along with a (possibly presigned) URL per underlying Parquet dataset.
5. User selects a report. App issues `read_parquet(url)`-based SQL against that dataset directly from the browser to S3, using HTTP range requests.
6. DuckDB-Wasm returns results as Apache Arrow to the main thread.
7. Visualization layer (ECharts/D3) renders the result set as the report's charts.
8. Further user interaction (filters, drill-downs, cross-filtering) re-issues SQL against the same in-browser DuckDB-Wasm instance — no network round-trip to any backend beyond the original Parquet fetch (subsequent queries against already-fetched row groups can be fully local; new predicates may trigger additional range requests for previously-unfetched byte ranges).

### 4.1 Query abstraction layer (recommended internal design)

Wrap all query issuance behind a small internal interface (e.g., `runQuery(sql): Promise<ArrowTable>`) rather than calling the DuckDB-Wasm connection directly from chart components. This is what lets you swap in a server-side DuckDB backend later (Mosaic's `socket`/`rest` client pattern is a good reference[^10]) if data volumes eventually exceed what's comfortable client-side, without touching any chart code.

### 4.2 Cross-filtering / linked views (nice-to-have, not required for v1)

If dashboards need clicking one chart to filter others, the Mosaic project's "selection" abstraction is a directly reusable open-source pattern for this — it generalizes Vega-Lite's selection model to coordinate predicates across multiple DuckDB-Wasm-backed views.[^22] Worth evaluating `@uwdata/vgplot` directly as a higher-level layer on top of DuckDB-Wasm for this specific feature rather than hand-building cross-filter logic, if linked/cross-filtered dashboards turn out to be a priority.

---

## 5. Security model

This is the section that needs the most deliberate design, precisely because it's the one place this architecture differs meaningfully from a traditional BI tool.

**The core constraint:** DuckDB-Wasm executes entirely inside the user's browser tab. Any Parquet bytes the browser fetches are, by construction, visible to that browser (and to anyone with access to that browser's dev tools, network tab, or downloaded cache). A `WHERE` clause or a role-based SQL view is not a security boundary here the way it might be against a server-side database — it's a UI convenience. **All real access control must happen before bytes leave S3, not inside the SQL the browser runs.**

**Practical implication for design:**
- Row-level security (e.g., "sales reps can only see their own region's rows") cannot be enforced by a client-side SQL filter alone, because a technically curious user could inspect network requests and see the full underlying Parquet file was fetched, filter or no filter. If row-level security is a real requirement, the options are: (a) partition data physically by the security boundary (one Parquet file/prefix per region) and gate *file-level* access via presigned URLs scoped per user, or (b) pre-materialize per-audience extracts server-side, or (c) fall back to a server-side query tier for that specific dataset. Given the current scope (single internal org, no stated row-level requirement), this can likely be deferred — flagging it now so it's a conscious decision, not a gap discovered later.
- Report-level access control (e.g., "only Finance can see the Finance dashboard") maps cleanly onto file/prefix-level S3 access control and is straightforward with presigned URLs or CloudFront signed cookies gated by the auth server's knowledge of AD group membership.
- Never put long-lived, broad-scope AWS credentials in client-side code. Every credential the browser touches should be short-lived and scoped to exactly the objects that request needs.

**v1 (no auth server) security posture:** since v1 is internal-only with no stated row-level requirement, the pragmatic starting point is a private (non-public) bucket with presigned URLs generated by a lightweight server-side endpoint gated by whatever the org's existing internal auth is (e.g., an SSO check in front of the static site, or even a simple shared-secret gate if the app is only reachable on the internal network) — deferring full AD/LDAP-group-aware entitlement logic to phase 2 as scoped.

---

## 6. Technology stack summary

| Layer | Choice | Notes |
|---|---|---|
| Query engine | DuckDB-Wasm (`@duckdb/duckdb-wasm`) | Runs in a Web Worker |
| Data format | Apache Parquet on S3 | Hive-style partitioning recommended |
| Standard charts | Apache ECharts (`echarts`) | Bar, pie, line, scatter, sankey, and more, natively |
| 3D charts | `echarts-gl` | WebGL, separate package |
| Raincloud plots | Custom D3 component | No off-the-shelf library covers this |
| Cross-filtering (optional) | `@uwdata/vgplot` (Mosaic) | Evaluate if linked dashboards become a priority |
| App framework | Any modern SPA framework (React/Vue/Svelte) | No architectural dependency on a specific one |
| Static hosting | Any host that supports custom response headers | Must set COOP/COEP |
| Auth (phase 2) | OIDC/SAML broker in front of AD/LDAP (e.g., Keycloak, or a managed IdP) | Never expose LDAP directly to the browser |

---

## 7. Non-functional requirements

**Browser support:** DuckDB-Wasm has been tested against Chrome, Firefox, Safari, and Node.js.[^1] Recommend targeting current-version evergreen browsers; Safari versions before 17 have known OPFS incompatibilities,[^23] which matters only for the optional local-caching feature, not core functionality.

**Performance targets (suggested, tune with real data):** initial report load — including first Wasm bundle fetch — under 3–5 seconds on a broadband connection after the bundle is cached by the browser; subsequent report switches and filter interactions under 500ms given the <100MB data scale in scope.

**Offline/caching:** treat OPFS-based local persistence as progressive enhancement only (Section 3.2), given inconsistent cross-browser support.[^14]

**Accessibility:** standard web accessibility practices apply to the app chrome; ECharts and D3 output should include appropriate ARIA labeling on interactive chart elements — this is a general implementation detail, not something specific to this architecture.

---

## 8. Phased roadmap

**Phase 1 (v1 — matches current scope):**
- Static web server + COOP/COEP headers.
- DuckDB-Wasm client-side query engine.
- Private S3 bucket, CORS-configured, accessed via presigned URLs from a minimal internal gate (not full AD/LDAP).
- ECharts for standard chart types; `echarts-gl` for 3D; custom D3 raincloud component.
- Report-level (not row-level) access control.

**Phase 2 (AD/LDAP):**
- Stand up the auth server (OIDC/SAML broker federated to AD/LDAP — build-vs-buy decision per Section 3.5).
- Wire report entitlement to AD group membership.
- Migrate presigned-URL issuance to be gated by the phase-2 auth server instead of the phase-1 minimal gate.

**Future scaling triggers (not required now, listed so the team recognizes them if they show up):**
- Row-level security becomes a real requirement → revisit Section 5's options (physical partitioning by security boundary, or a server-side query tier for that dataset).
- Typical report size grows well past the current <100MB scope and starts approaching the multi-GB range → revisit the query abstraction layer (Section 4.1) to add an optional server-side DuckDB tier for those specific heavy reports, following the Mosaic `socket`/`rest` client pattern as a reference architecture,[^10] while keeping the fully client-side path for everything else.

---

## References

[^1]: DuckDB-Wasm — GitHub repository. https://github.com/duckdb/duckdb-wasm
[^2]: A DuckDB-Wasm Web Mapping Experiment with Parquet — Sparkgeo. https://sparkgeo.com/blog/a-duckdb-wasm-web-mapping-experiment-with-parquet/
[^3]: Extensions — DuckDB documentation (Wasm extension differences, HTTPFS/CORS notes). https://duckdb.org/docs/lts/clients/wasm/extensions
[^4]: The Best JavaScript Chart Libraries for 2026 — usedatabrain.com. https://www.usedatabrain.com/blog/javascript-chart-libraries
[^5]: Apache ECharts Sankey source (feature reference). https://github.com/apache/echarts/blob/master/src/chart/sankey/SankeyView.ts
[^6]: echarts-gl — GitHub repository. https://github.com/ecomfe/echarts-gl
[^7]: Raincloud Plots — reference D3 implementation (gist). https://gist.github.com/vijithassar/c60dafea4431f292660d6f5e0487e470
[^8]: Apache ECharts — Features. https://echarts.apache.org/en/feature.html
[^9]: OIDC, SAML, LDAP – Choosing the Right Identity Stack — GetSetLive. https://blogs.getsetlive.com/oidc-saml-ldap-choosing-the-right-identity-stack/
[^10]: Mosaic: An Architecture for Scalable & Interoperable Data Views — UW Interactive Data Lab (IEEE VIS 2024). https://idl.uw.edu/papers/mosaic ; GitHub: https://github.com/uwdata/mosaic
[^11]: DuckDB Wasm: Analytical SQL Database in Your Browser — MotherDuck. https://motherduck.com/blog/duckdb-wasm-in-browser/
[^12]: What is the size limit of DuckDB in Wasm? — GitHub Discussion. https://github.com/duckdb/duckdb-wasm/discussions/1241
[^13]: DuckDB-Wasm: Efficient Analytical SQL in the Browser — DuckDB blog. https://duckdb.org/2021/10/29/duckdb-wasm
[^14]: DuckDB and OPFS for Browser Storage — Mark Wylde. https://markwylde.com/blog/duckdb-opfs-todo-list/
[^15]: A DuckDB-Wasm Web Mapping Experiment with Parquet — Sparkgeo (bundle size observation). https://sparkgeo.com/blog/a-duckdb-wasm-web-mapping-experiment-with-parquet/
[^16]: Understanding SharedArrayBuffer and cross-origin isolation — LogRocket. https://blog.logrocket.com/understanding-sharedarraybuffer-and-cross-origin-isolation/
[^17]: Allow setting COOP and COEP headers in GitHub Pages — GitHub Discussion (workaround reference). https://github.com/orgs/community/discussions/13309
[^18]: DuckDB Wasm: Analytical SQL Database in Your Browser — MotherDuck (parquet_schema/metadata inspection). https://motherduck.com/blog/duckdb-wasm-in-browser/
[^19]: Configure and confirm CORS in Amazon S3 — AWS re:Post. https://repost.aws/knowledge-center/s3-configure-cors ; Using cross-origin resource sharing (CORS) — AWS S3 docs. https://docs.aws.amazon.com/AmazonS3/latest/userguide/cors.html
[^20]: How to Fix S3 CORS Errors in Browser Applications — oneuptime.com. https://oneuptime.com/blog/post/2026-02-12-fix-s3-cors-errors-in-browser-applications/view
[^21]: The Best JavaScript Chart Libraries for 2026 — usedatabrain.com (ECharts performance/sampling notes). https://www.usedatabrain.com/blog/javascript-chart-libraries
[^22]: Mosaic: An Architecture for Linking Databases and Scalable Interactive Visualizations — UW IDL (ACM SIGMOD 2025 companion). https://idl.uw.edu/papers/mosaic-sigmod-demo
[^23]: Persistent Storage Options — SQLite Wasm docs (Safari OPFS version note, applies to the same browser-API constraint DuckDB-Wasm inherits). https://sqlite.org/wasm/doc/trunk/persistence.md
