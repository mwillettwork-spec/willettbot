# WillettBot Privacy Policy

**Effective date:** May 6, 2026
**Last updated:** May 6, 2026

This Privacy Policy explains how **WillettBot Inc.**, a New York corporation
with its principal office at 66 Ulster Ave, Atlantic Beach, NY 11509 ("we,"
"us," or "WillettBot"), handles your information when you use the WillettBot
desktop application ("the App") and the willettbot.com website ("the Site").

## Summary in Plain English

* The App runs on your computer. Scripts you record and the actions inside
  them stay in a folder on your computer and are never uploaded to us.
* To use the App you create an account on willettbot.com using your email
  address. We store your email and the metadata about your subscription.
* We do not collect telemetry about how you use the App, your keystrokes,
  or the contents of your scripts.
* When the App checks for software updates, it makes a request to GitHub.
  When the App verifies your subscription, it makes a request to our
  server. Both requests include your IP address.

## What WillettBot Records on Your Computer

When you use the recording feature, the App captures every keystroke and
mouse action you perform until you stop the recording, including any text
you type while recording is active. **This includes passwords, credit card
numbers, and other sensitive information if you type them while recording.**
The App displays a clear warning before and during every recording reminding
you not to enter sensitive information.

Recorded scripts, workflows, favorites, and scheduled runs are saved as
JSON files on your computer:

```
macOS:    ~/Library/Application Support/WillettBot/
Windows:  %APPDATA%\WillettBot\
```

These files stay on your computer. They are not uploaded, transmitted, or
otherwise shared by the App. You can read, edit, copy, or delete them at
any time using any text editor.

If your computer is backed up by Time Machine, iCloud Drive, OneDrive,
Dropbox, or similar, those backup tools may copy your scripts folder to
their servers. WillettBot does not perform those backups; they are
configured separately by you in your operating system or third-party
software.

## Information We Do Not Collect

We do **not** collect:

* Telemetry, usage analytics, or crash reports about how you use the App
* The content of your scripts, recordings, workflows, or anything you
  automate
* Your keystrokes, clipboard contents, or screen contents
* Cookies for advertising, advertising identifiers, or browsing history
* Anything else, except as explicitly listed below

## Information You Provide When You Sign Up

To use the App you sign in at willettbot.com with an email address, which
is verified by clicking a magic link we send you. We use **Clerk, Inc.**
to handle the authentication flow. Clerk stores your email address and a
unique user identifier; their privacy policy is at
<https://clerk.com/legal/privacy>.

After you sign in, the desktop App requests a device token. The token is
stored in:

```
macOS:    ~/Library/Application Support/WillettBot/account.json
Windows:  %APPDATA%\WillettBot\account.json
```

The App sends this token to our server when it checks whether your
subscription is active. The token can be revoked at any time by signing
out from the App or by contacting us.

## Information You Provide When You Subscribe

When you subscribe to a paid plan, we direct you to **Stripe, Inc.** to
complete payment. Stripe collects and processes your payment information
(credit card number, billing address, etc.) directly. We do not see, store,
or have access to your full payment-card details. Stripe's privacy policy
is at <https://stripe.com/privacy>.

We receive from Stripe and store on our servers:

* A Stripe customer identifier
* Your subscription status (active, canceled, past due, etc.)
* The plan you chose (monthly or yearly)
* The current billing period and renewal date
* Whether you have asked to cancel at the end of the current period

This information is stored in a database hosted by **Supabase, Inc.** Their
privacy policy is at <https://supabase.com/privacy>. The willettbot.com
website is hosted by **Vercel Inc.**; their privacy policy is at
<https://vercel.com/legal/privacy-policy>.

We use this information solely to: (a) determine whether your desktop App
should be unlocked, (b) display your account and subscription status on the
willettbot.com dashboard, (c) email you about subscription events
(renewal, cancellation, payment failure), and (d) provide customer
support. We do not sell, rent, or share this information with anyone for
their own marketing purposes.

## Information We Receive From Third Parties

* **GitHub.** The App checks for software updates by reading the public
  list of releases at <https://github.com/mwillettwork-spec/willettbot/releases>.
  GitHub may log the IP address making the request. We do not have access
  to those logs. GitHub's privacy practices are at
  <https://docs.github.com/en/site-policy/privacy-policies>.
* **Apple.** When you install or update the App on macOS, Apple may
  perform a notarization check by contacting Apple's servers. We do not
  see the contents of that check. Apple's privacy practices are at
  <https://www.apple.com/legal/privacy/>.
* **Microsoft.** When you install or update the App on Windows, Microsoft
  may perform a SmartScreen reputation check. We do not see the contents
  of that check. Microsoft's privacy practices are at
  <https://privacy.microsoft.com/>.

## Children

The App and Site are not directed to children under 13. We do not
knowingly create accounts for, or accept payment from, anyone under 13.
If you believe a child has signed up, contact us and we will delete the
account.

## Security

We sign and notarize the App through Apple, and code-sign the Windows
installer, so the binary you download is verified to come from us and has
not been modified in transit. Your scripts and the device token are stored
in a folder readable only by your operating-system user account.

For the willettbot.com website, all traffic is encrypted in transit with
TLS. The Supabase database is reachable only via service-role credentials
held in our hosting environment; user data is segregated by Clerk user
identifier. Stripe handles all payment-card data directly; we never store
full card numbers on our servers.

## Your Rights

If you are in a jurisdiction with data-protection laws (such as the
California Consumer Privacy Act or the EU General Data Protection
Regulation), you have rights including the right to access, correct, or
delete the personal information we hold about you, and to ask us to stop
sending you account-related emails.

To exercise any of these rights, contact us at the address below. We will
respond within 30 days.

You can also delete your account directly: sign in at willettbot.com,
cancel your subscription, and email us to delete your account record.
You may delete the App at any time by dragging it to the Trash (macOS) or
using Settings → Apps (Windows). To remove all locally stored WillettBot
data, delete the WillettBot folder shown above.

## International Users

WillettBot Inc. is a New York corporation. Our servers and processors
(Clerk, Stripe, Supabase, Vercel) are located in the United States. By
using the App or Site you consent to the transfer of your information to
the United States.

## Changes to This Policy

If we materially change how we handle information, we will update this
document, change the "Last updated" date at the top, and email a
notification to the address on file for your account.

## Contact

Questions about this Privacy Policy can be sent to:

**WillettBot Inc.**
66 Ulster Ave
Atlantic Beach, NY 11509
**mwillettwork@gmail.com**
