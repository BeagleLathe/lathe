---
description: Update BeagleLathe to the latest version.
---

Run the following command using the Bash tool:

```bash
pip install --upgrade git+ssh://git@github.com/pbehrens/beagle-lathe.git
```

After updating, tell the user: "Restart Claude Code (or run /mcp restart) for changes to take effect."

If pip install fails with a network or auth error, suggest the user check their SSH key or internet connection.

If the user installed from a local clone (they will know), use:
```bash
git -C /path/to/their/beagle-lathe pull --ff-only && pip install -e /path/to/their/beagle-lathe
```
