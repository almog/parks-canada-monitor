* Auth credential handling is underspecified. The doc mentions storing Parks Canada credentials in .env and doing Google/GCKey login via Playwright, but doesn't address session token management, MFA, or what happens when sessions expire mid-run.
* Missing observability story. There's structured logging mentioned but no discussion of metrics, health checks, or how you'd know the tool is silently failing on a VPS at 3 AM.
* No concurrency model. The doc says ~20 HTTP GETs per cycle but doesn't discuss whether these are sequential or concurrent, or how the scheduler interacts with a long-running booking flow.
* Watchlist hot-reload isn't addressed. It says "user edits watchlist" in the flow diagram but doesn't clarify whether changes require a restart.
