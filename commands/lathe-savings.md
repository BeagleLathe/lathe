---
description: Show local savings counter — calls saved, tokens saved, and estimated cost saved vs vanilla Claude Code.
---

Run the following command using the Bash tool:

```bash
beaglelathe savings
```

Show the output to the user verbatim. The output looks like:

```
total calls:   127
calls saved:   254  (vs vanilla Claude Code)
tokens saved:  127,000
cost saved:    $0.38  (est., Claude Sonnet rate)

by tool:
  search      45 calls  →  90 saved
  edit        38 calls  →  76 saved
  read        30 calls  →  30 saved
  sh          14 calls  →   7 saved
```

These numbers are computed locally from ~/.beaglelathe/state.db. Nothing is sent to a server.

The savings model: each BeagleLathe `search` call replaces ~3 vanilla Claude Code calls (glob + grep + read); each `edit` replaces ~3 calls (read + edit + verify). Tokens saved is estimated at 500 tokens per replaced call. Cost is estimated at Claude Sonnet rates.

If output says "No calls recorded yet", explain that savings accumulate as the user runs BeagleLathe tools in this and future sessions.
