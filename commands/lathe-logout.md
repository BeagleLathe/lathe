---
description: Clear stored BeagleLathe credentials from ~/.beaglelathe/credentials.json.
---

Run the following command using the Bash tool:

```bash
beaglelathe logout
```

Show the output to the user. If it reports "Cleared credentials", confirm: "Logged out. The MCP tools remain active but savings will no longer sync to your account. Run /lathe-login to sign back in."

If it reports "No credentials file to clear", tell the user they were not logged in.
