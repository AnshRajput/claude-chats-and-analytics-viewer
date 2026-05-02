"""Dashboard subpackage — codeburn-style observability for Claude Code.

Exposes aggregators and analyzers consumed by the HTTP handler:

* :mod:`aggregator` — top-level ``/api/dashboard`` payload
* :mod:`optimize` — waste-pattern scanner + A-F health grade
* :mod:`compare` — side-by-side model comparison
* :mod:`yield_tracker` — git-commit correlation (productive/reverted/abandoned)
* :mod:`plans` — subscription plan tracking
* :mod:`export` — CSV/JSON multi-period export
* :mod:`period` — common date-range parsing
"""
