---
name: provisioning-user-accounts
description: Use when creating, inviting, activating, deactivating, or changing access roles for user accounts in any application, service, or production environment.
---

# Provisioning user accounts

Grant the smallest access scope that satisfies the request. An ambiguous “admin”
request is not permission to grant global administration or superuser access.

## Question gate

When information is missing, ask these grouped questions before a mutation. Ask
only missing facts; if the request is complete, do not ask ritual questions.

1. **Person and target:** What are the exact account identifier (usually email)
   and display name; which environment or tenant is targeted?
2. **Access and activation:** Is this a new or existing account; what exact role
   and resource scope are intended; is full-instance/superuser access explicitly
   intended; should the user establish credentials via a trusted email invite or
   reset flow?

Never infer an address from a name, environment from context, access scope from
“admin”, or a password-delivery method from an incomplete request.

## Safe workflow

1. **Authorize.** A mutation to a live environment requires an explicit request
   naming that environment and the precise access scope. Read-only account and
   membership checks are safe preparation.
2. **Inspect.** Find the account by its exact identifier. For an existing
   account, preserve every unrelated role, group, resource grant, credential,
   and active session unless removal is named.
3. **Grant least privilege.** Use the application’s existing role/group model;
   bind roles to the named project, workspace, tenant, or resource where the
   system supports scope. Grant global administration or superuser only when
   the request says so explicitly.
4. **Activate securely.** For a new account, prefer the product’s invitation or
   password-setup flow. Do not generate, disclose, or paste passwords, reset
   links, API keys, or authentication tokens into chat.
5. **Verify.** Confirm the exact identity, active state, requested role and
   scope, absence of unintended elevated access, and the result of the
   invitation/email job. “Accepted by the mail worker” is not proof of inbox
   receipt.

## Common mistakes

| Mistake | Correct action |
| --- | --- |
| “Make them admin” | Ask whether this means a named resource, tenant-wide administration, or full-instance control. |
| Account may exist | Read-only lookup first; update the existing account minimally. |
| “Send the password by email” | Send a trusted invitation/reset flow, never a password. |
| Urgent request | Urgency does not remove identity, environment, or scope requirements. |
| Bulk request | Require one explicit role/scope and identity list; do not silently apply one person’s access to all. |

## Evidence to report

Report target environment, account identifier, display name, exact granted scope,
activation state, and mail-task status. Never report secrets or reset URLs.
