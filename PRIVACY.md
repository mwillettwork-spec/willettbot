# WillettBot Privacy Policy

**Effective date:** April 24, 2026
**Last updated:** April 24, 2026

This Privacy Policy explains how Myles Willett ("we," "us," or "WillettBot")
handles your information when you use the WillettBot desktop application
("the App").

## Summary in Plain English

* WillettBot runs entirely on your computer. It does not send any data
  about how you use it to us or anyone else.
* Scripts you record and the actions inside them stay in a folder on your
  Mac and are never uploaded.
* When you activate the App with a license key, your email address and the
  expiration date inside the key are stored locally so the App knows it is
  activated. We do not store your activation on a server.
* When the App checks for software updates, it makes a network request to
  GitHub to look at the public list of WillettBot releases. That request
  includes your IP address, which GitHub may log. We do not see those logs.

## What WillettBot Records on Your Computer

When you use the recording feature, WillettBot captures every keystroke and
mouse action you perform until you stop the recording, including any text
you type while recording is active. **This includes passwords, credit card
numbers, and other sensitive information if you type them while recording.**
The App displays a clear warning before and during every recording reminding
you not to enter sensitive information.

Recorded scripts are saved as JSON files in:

```
~/Library/Application Support/WillettBot/scripts/
```

These files stay on your computer. They are not uploaded, transmitted, or
otherwise shared by the App. You can read, edit, copy, or delete them at
any time using any text editor.

If your computer is backed up by Time Machine, iCloud Drive, Dropbox, or
similar, those backup tools may copy your scripts folder to their servers.
WillettBot does not perform those backups; they are configured separately
by you in macOS or third-party software.

## Information We Collect

We collect **none of the following**:

* Telemetry, usage analytics, or crash reports
* Your scripts, recordings, or any content of what you automate
* Your keystrokes, clipboard contents, or screen contents
* Your IP address, device identifiers, or location
* Cookies, advertising identifiers, or browsing history
* Anything else, unless explicitly listed in the next section

## Information You Provide When Activating

When you enter a license key to activate WillettBot, the key contains your
email address and an expiration date, which the App reads locally to verify
your activation. The license key is stored in:

```
~/Library/Application Support/WillettBot/activation.json
```

We separately keep a record of email addresses to whom we have issued
license keys, on a personal computer or trusted cloud document we control.
This list is used solely to: (a) re-issue a key if you lose yours, (b) revoke
a key if it is suspected of being misused, and (c) contact you about
material updates or expiration. We do not sell, rent, or share this list
with anyone.

## Information We Receive From Third Parties

* **GitHub.** WillettBot checks for software updates by reading the public
  list of releases at <https://github.com/mwillettwork-spec/willettbot/releases>.
  GitHub may log the IP address making the request. We do not have access
  to those logs and do not associate them with your license. GitHub's
  privacy practices are at <https://docs.github.com/en/site-policy/privacy-policies>.
* **Apple.** When you install or update WillettBot on macOS, Apple may
  perform a notarization check by contacting Apple's servers. We do not see
  the contents of that check. Apple's privacy practices are at
  <https://www.apple.com/legal/privacy/>.

## Children

WillettBot is not directed to children under 13. We do not knowingly issue
license keys to anyone under 13. If you believe a child has activated the
App, contact us and we will revoke the key.

## Security

We sign and notarize WillettBot through Apple so the binary you download is
verified to come from us and has not been modified in transit. Your scripts
and license file are stored in a folder readable only by your macOS user
account. We rely on macOS's permission model (Accessibility, Automation,
Input Monitoring) to gate the App's ability to read input and control other
apps; you grant or deny each of these permissions explicitly through System
Settings.

We are a single-person operation and our security guarantees reflect that:
the App does not communicate with any server we operate, so there is no
backend to breach. The license-key list described above is stored on a
device we control with a strong password and full-disk encryption.

## Your Rights

Because we collect almost nothing about you, most data-protection rights
(access, deletion, portability) are satisfied automatically by the fact
that the data lives on your computer, not ours. To exercise any right
relating to the limited license-key information we keep, contact us at
**mwillettwork@gmail.com** and we will respond within 30 days.

You may delete WillettBot at any time by dragging it to the Trash. To
remove all locally stored WillettBot data, delete the
`~/Library/Application Support/WillettBot/` folder.

## Changes to This Policy

If we materially change how WillettBot handles information, we will update
this document, change the "Last updated" date at the top, and (for
registered license-key holders) email a notification to the address on file.

## Contact

Questions about this Privacy Policy can be sent to:

**Myles Willett — mwillettwork@gmail.com**
