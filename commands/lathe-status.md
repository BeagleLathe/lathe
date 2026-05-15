---
description: Show BeagleLathe account status — plan, calls used this month, budget remaining.
---

Run the following command using the Bash tool:

```bash
beaglelathe status
```

Show the output to the user verbatim. The output looks like:

```
plan:       Free tier
calls used: 42 this month
remaining:  158 calls
resets:     2026-06-01T00:00:00+00:00
account:    user@example.com
```

If the command exits with code 1 and says "Not logged in", tell the user to run /lathe-login.

If it shows a network warning and cached data, explain: "The backend is unreachable right now. Showing locally cached values — run this again when your connection is restored."

If budget_remaining is 0 or the output mentions quota exceeded, suggest: "You've used your free-tier calls this month. Run /lathe-upgrade to continue."
