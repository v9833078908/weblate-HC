---
name: provisioning-weblate-users
description: Use when creating, inviting, activating, or changing the role of a user on this HCGameLoc Weblate instance, including project administrators, site-wide administrators, superusers, and password-setup emails.
---

# Provisioning Weblate users

Use the smallest access scope that satisfies the request. A project administrator
is **not** a site administrator or a superuser.

## Scope

Only HCGameLoc Weblate: the local dev container or production
`l10n.herocraft.com`. This is an operational skill; do not use it for source-code
changes to authentication or permissions.

Production user, group, role, password, or invitation changes require the
user’s explicit instruction for **production**. A direct request that names
production and the exact access scope is that approval. Read-only existence and
membership checks do not require it.

## Question gate

If any required fact is absent, ask these compact grouped questions before any
mutation. Do not invent values from a name, email domain, or previous request.

1. **Person and target:** What are the exact email and profile name; is this
   `dev` or production?
2. **Access and activation:** Is the account new or existing; which exact
   project slug(s) and role(s); is `superuser` explicitly intended; should the
   user set their password via an email invitation/reset link?

Ask only the missing parts. If all facts are present, act; do not ask ritual
questions. “Admin” without a project or an explicit site-wide scope is
insufficient. “Highest access”, “full instance access”, or “superuser” is an
explicit superuser request; “project admin” is not.

## Role selection

| Requested outcome | Grant |
| --- | --- |
| Viewer, translator, reviewer | Membership in the existing team with that role, scoped to the named project. |
| Ordinary project administrator | Membership in the named project’s **Administration** team. It grants access management only in that project. |
| Site-wide user administrator | A suitable existing site-wide team carrying `user.edit`; confirm that instance-wide user management is intended. |
| Superuser | `is_superuser=True`, only after explicit request. It bypasses all project and permission scopes. |

Never substitute `Managers`, a site-wide team, or `is_superuser` for a
project-scoped `Administration` grant. Never remove existing roles, teams, or
project access unless the request names the removal.

## Execution

1. Resolve the project slug and role against the instance. For a named email,
   make a **read-only** check for an existing account and its current teams.
2. Existing account: change only the requested membership/flag. New account:
   use the Weblate invitation flow so it receives the password-setup email;
   do not create a duplicate account or disclose a generated password.
3. Prefer the connected Weblate MCP for live-instance operations. If it is not
   available, use the Weblate UI or the repository’s approved production access
   path (`./deploy/vps.sh`) only after the mutation is authorized.
4. For a new superuser, create an active account with an unusable password,
   then invoke Weblate’s normal password-reset/invitation flow. Do not send
   password-reset tokens, links, API keys, or passwords in chat.
5. Verify after the mutation: exact email and full name, active status, the
   requested project-scoped membership or site-wide flag, absence of unintended
   `is_superuser`, and successful invitation/email task delivery.

## Common mistakes

- Treating “admin” as superuser. Ask whether it means one project, site-wide
  user management, or full instance control.
- Guessing an email or environment from a person’s name. Ask the grouped
  question instead.
- Creating a new account before checking for an existing one. Preserve the
  account and add only the requested access.
- Giving the `Administration` team language limits. Its project-wide access
  does not apply when language-limited.
- Calling a queued email “delivered” without its worker result. Report task
  success as sending accepted by SMTP; inbox receipt remains external.

## Evidence to report

State the instance, username/email, profile name, exact role scope, whether the
account is active, and email-task status. Do not expose credentials or links.
