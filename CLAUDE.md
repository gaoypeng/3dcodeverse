# 3dcodeverse (repo root)

The harness lives in **`harness/`** — read `harness/CLAUDE.md` +
`harness/docs/ARCHITECTURE.md` before touching it.  Install with
`pip install -e harness`.  Run tests with
`cd harness && python -m pytest tests -q -m "not live"`.

Other top-level folders are separate components (datasets, web, papers).
