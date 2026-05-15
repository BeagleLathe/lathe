---
description: Sign in to BeagleLathe via magic link. Opens browser, polls for confirmation, writes credentials.
---

Run the BeagleLathe login flow using the Bash tool:

```bash
beaglelathe login
```

The command will:
1. Request a magic-link URL from the auth backend.
2. Print the URL and attempt to open it in the default browser.
3. Wait until the user clicks the link and confirms sign-in.
4. Write credentials to ~/.beaglelathe/credentials.json.

Show the command's stderr output to the user verbatim so they can see the login URL and confirmation status.

If the `beaglelathe` binary is not found, tell the user to run `pip install -e .` from the beagle-lathe repo directory first.

If login succeeds, confirm: "Signed in. Run /lathe-status to see your plan and remaining calls."
