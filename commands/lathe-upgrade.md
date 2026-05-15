---
description: Open Stripe checkout to upgrade from Free tier to Pro.
---

Run the following command using the Bash tool:

```bash
beaglelathe upgrade
```

The command will call POST /billing/checkout to get a Stripe Checkout URL, then attempt to open it in the default browser. Show the printed URL to the user so they can open it manually if the browser doesn't launch.

If the command says "Not logged in", tell the user to run /lathe-login first.

If it reports a network error, say: "Could not reach the billing server. Check your connection and try again, or run `beaglelathe login` to refresh your session if your token may have expired."

After a successful upgrade, the user can run /lathe-status to confirm their plan has changed to Pro.
