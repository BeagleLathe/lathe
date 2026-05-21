"""large-file-search: find a specific value buried in a large file's method bodies.

Exercises BL's AST-truncated read (mode=truncated default; mode=structure
for declarations-only; mode=full symbol= for one body verbatim).

v2 (scaled): 25 classes, each with longer bodies + richer docstrings, totalling
~1500 lines. At that size the cost of vanilla's "Read the whole file" path
becomes meaningful relative to BL's structure-then-targeted-read path.

Two viable BL strategies:
  (a) search for the string "authoritative" — one call surfaces the unique
      hit with snippet + enclosing context; the enclosing class is obvious.
  (b) read mode=truncated to enumerate classes, then read mode=full
      symbol=<ClassName>.handle on each candidate until one returns the
      target string.

Either path stays roughly constant-cost as the file grows; vanilla's full
Read scales linearly with file size.
"""

from __future__ import annotations

from pathlib import Path

CLASSES: list[str] = [
    "AuthProvider",
    "BackupRunner",
    "CacheLayer",
    "DatabaseConnection",
    "EventBus",
    "FileWatcher",
    "GeoLocator",
    "HealthChecker",
    "IndexBuilder",
    "JobQueue",
    "KeyRotator",
    "LogAggregator",
    "MetricsReporter",
    "NotificationService",
    "OrderProcessor",
    "PaymentGateway",
    "QuotaTracker",
    "RateLimiter",
    "SessionManager",
    "TaskScheduler",
    "UserDirectory",
    "ValidationEngine",
    "WebhookDispatcher",
    "XmlSerializer",
    "YamlLoader",
]

# The one class whose handle() returns "authoritative". Placed in the middle
# of the list so an agent that bails after reading the first few classes
# won't accidentally win.
TARGET_CLASS: str = "OrderProcessor"


def _class_block(name: str, is_target: bool) -> str:
    returns = '"authoritative"' if is_target else f'"{name.lower()}-default"'
    return (
        f"class {name}:\n"
        f'    """{name} primitive — internal API surface.\n'
        f"\n"
        f"    This primitive participates in the standard service lifecycle:\n"
        f"    construct, :meth:`initialize`, then any number of :meth:`handle`\n"
        f"    calls, then :meth:`teardown` at shutdown. The orchestrator wires\n"
        f"    all primitives together in dependency order and calls them in\n"
        f"    parallel when their inputs are ready.\n"
        f"\n"
        f"    Configuration is taken from the ``config`` dict at construction\n"
        f"    time. Anything not present in ``config`` falls back to module-\n"
        f"    level defaults; see the module docstring for the schema.\n"
        f"\n"
        f"    Thread-safety: instances are NOT safe for concurrent use from\n"
        f"    multiple threads. The orchestrator guarantees serialised access.\n"
        f'    """\n'
        f"\n"
        f"    def __init__(self, config: Optional[dict] = None) -> None:\n"
        f"        self.config = config or {{}}\n"
        f"        self._initialized = False\n"
        f"        self._cache: dict[str, Any] = {{}}\n"
        f"        self._counter = 0\n"
        f"        self._last_event: Optional[dict] = None\n"
        f"        self._error_count = 0\n"
        f"\n"
        f"    def initialize(self) -> None:\n"
        f'        """Set up the resources required by this provider.\n'
        f"\n"
        f"        Idempotent: calling initialize() on an already-initialised\n"
        f"        instance is a no-op. The orchestrator may call this more\n"
        f"        than once during recovery.\n"
        f'        """\n'
        f"        if self._initialized:\n"
        f"            return\n"
        f"        self._initialized = True\n"
        f"        self._cache.clear()\n"
        f"        self._counter = 0\n"
        f"        self._error_count = 0\n"
        f"\n"
        f"    def handle(self, event: dict) -> str:\n"
        f'        """Dispatch a domain event and return a status string.\n'
        f"\n"
        f"        Returns the canonical status string for this provider. The\n"
        f"        orchestrator uses this value to route follow-up work.\n"
        f'        """\n'
        f"        if not self._initialized:\n"
        f"            self.initialize()\n"
        f"        self._counter += 1\n"
        f"        self._last_event = event\n"
        f"        # ... domain-specific handling here ...\n"
        f"        return {returns}\n"
        f"\n"
        f"    def teardown(self) -> None:\n"
        f'        """Release any resources held by this provider."""\n'
        f"        self._cache.clear()\n"
        f"        self._last_event = None\n"
        f"        self._initialized = False\n"
        f"\n"
        f"    def is_healthy(self) -> bool:\n"
        f'        """Best-effort health check used by the orchestrator."""\n'
        f"        return self._initialized and self._error_count < 10\n"
        f"\n"
        f"    def stats(self) -> dict:\n"
        f'        """Return runtime statistics for instrumentation."""\n'
        f"        return {{\n"
        f'            "cache_size": len(self._cache),\n'
        f'            "initialized": self._initialized,\n'
        f'            "events_handled": self._counter,\n'
        f'            "errors": self._error_count,\n'
        f"        }}\n"
        f"\n"
        f"    def reset_errors(self) -> None:\n"
        f'        """Clear the error counter after operator acknowledgement."""\n'
        f"        self._error_count = 0\n"
        f"\n"
        f"    def describe(self) -> str:\n"
        f'        """Return a one-line identifier for log output."""\n'
        f"        state = \"ready\" if self._initialized else \"cold\"\n"
        f"        return f\"<{name}:{{state}}:{{self._counter}}>\"\n"
        f"\n"
    )


def _make_file_content() -> str:
    header = (
        '"""src.library: collection of internal service primitives.\n'
        "\n"
        "Each class implements the same lifecycle (initialize, handle, teardown,\n"
        "is_healthy, stats, reset_errors, describe). The orchestrator iterates\n"
        "over all of them at startup. Adding a new primitive is a matter of\n"
        "dropping in one more class that follows the same shape.\n"
        "\n"
        "Schema for ``config``:\n"
        "  - timeout_ms (int): per-call timeout, default 5000\n"
        "  - retries (int): retry budget per event, default 3\n"
        "  - backoff_base (float): exponential-backoff base, default 1.5\n"
        "  - error_threshold (int): errors before is_healthy goes False, default 10\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from typing import Any, Optional\n"
        "\n"
        "\n"
    )
    body = "\n".join(_class_block(c, c == TARGET_CLASS) for c in CLASSES)
    return header + body


def setup(root: Path) -> None:
    f = root / "src/library.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_make_file_content(), encoding="utf-8")
