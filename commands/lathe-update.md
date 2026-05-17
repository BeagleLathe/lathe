---
description: Update BeagleLathe to the latest version.
---

Run the upgrade for whichever Python installer the user has. Try in order — only fall through on a missing-tool error. Use the Bash tool.

```bash
uv tool upgrade beaglelathe || pipx upgrade beaglelathe || pip install --upgrade beaglelathe
```

After the upgrade completes, tell the user: "Restart Claude Code (or run /mcp restart) for changes to take effect."

If every installer fails with "not found", the user doesn't have a Python installer set up — point them at the install instructions: https://beaglelathe.dev/docs.html#install

If the upgrade fails with a network or permissions error, suggest the user re-run `curl -LsSf https://beaglelathe.dev/install.sh | sh` to refresh the install.
