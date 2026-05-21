"""hidden-bug-in-body: pass iff login() behaves correctly across a small truth table."""

from __future__ import annotations

from pathlib import Path

CASES = [
    (("admin", "secret"), True),
    (("admin", "wrong"), False),
    (("alice", "secret"), False),
    (("alice", "wrong"), False),
    (("", ""), False),
]


def verify(root: Path) -> tuple[bool, str]:
    auth_path = root / "src/auth.py"
    if not auth_path.exists():
        return False, "src/auth.py missing"
    text = auth_path.read_text(encoding="utf-8")
    try:
        ns: dict = {}
        exec(compile(text, str(auth_path), "exec"), ns)
    except Exception as e:  # noqa: BLE001
        return False, f"src/auth.py failed to import: {e}"
    login = ns.get("login")
    if not callable(login):
        return False, "login() missing or not callable"

    failures: list[str] = []
    for args, want in CASES:
        try:
            got = login(*args)
        except Exception as e:  # noqa: BLE001
            failures.append(f"login{args!r} raised {e!r}")
            continue
        if got != want:
            failures.append(f"login{args!r}={got!r}, want {want!r}")
    if failures:
        return False, "; ".join(failures)
    return True, f"all {len(CASES)} truth-table cases pass"
