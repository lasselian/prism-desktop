# Security policy

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately
through GitHub instead: go to the repository's **Security** tab and choose
**Report a vulnerability**
([direct link](https://github.com/lasselian/prism-desktop/security/advisories/new)).

Helpful details:

- what an attacker can do, and what they need first (network access, a local
  account, a malicious Home Assistant entity, …)
- steps to reproduce, and the Prism version and operating system
- a suggested fix, if you have one

You'll get a reply in the advisory. Fixes are developed privately and
published together with the advisory and a release; reporters are credited
unless they'd rather not be.

## Supported versions

Security fixes go into the latest release. Prism updates itself (Settings →
**Check for Updates**), so please make sure you are on the newest version before
reporting.

## Scope

In scope: the Prism Desktop app and its installers, including how it stores
your Home Assistant token, talks to Home Assistant, handles notifications and
updates itself.

Out of scope: vulnerabilities in Home Assistant itself (report those to the
[Home Assistant project](https://www.home-assistant.io/security/)) and in
third-party libraries, unless Prism uses them in an unsafe way.
