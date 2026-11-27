---
name: rhcl-upstream-tracing
description: Reason about RHCL and Kuadrant releases, map component versions and OpenShift image variants, plan upstream console-plugin releases, choose Jira fixVersions, and verify what a downstream release shipped.
---

# RHCL and Kuadrant releases

Keep four identities separate: RHCL (Red Hat Connectivity Link) product release,
upstream operator release, component release, and image digest. Similar version numbers establish no mapping.

## Start with the question

- For available releases, lifecycle status or Jira changes, read
  [sources and Jira](references/sources-and-jira.md).
- For console-plugin branch selection or a release plan, read
  [console releases](references/console-releases.md).
- For whether a fix shipped, read
  [artifact tracing](references/artifact-tracing.md).
- For known version relationships, consult the dated
  [release observations](references/release-observations.md), then refresh the
  relevant live sources. Observations are evidence to revisit, not standing orders.

## Reasoning rules

1. Establish the RHCL stream, component, intended OCP versions and user-selected
   scope. Track deferred branches explicitly. A new observation does not reopen
   a branch the user excluded.
2. Resolve source facts separately from decisions. A Jira target is a plan;
   a merged PR is source availability; a tag is a source release; a successful
   image build is an artifact; a released bundle establishes shipped inclusion.
3. Map each component independently. Resolve operator dependency pins, product-build
   submodules and image-selection code. Include every selected image variant in
   a coverage assessment, even when publication of a variant is out of scope.
4. Resolve contradictions before making a shipping or support claim. Report the
   conflicting sources and the remaining gap. Take support status from the
   lifecycle API and policy, not from Jira state.
5. Apply only authorised external changes. A release plan does not authorise tags,
   pushes, GitHub releases, Jira changes or downstream updates. Existing explicit
   authorisation remains valid; ask only for a genuinely missing decision.
6. Verify mutations by reading back the affected fields or artifacts. Keep user
   targets, source-fix coverage and actual shipment distinct in the result.

For console fix work, the usual upstream deliverable is reviewed fixes on the
selected release branches plus their releases. Product-build pin changes, RHCL
image builds and RHCL z-stream publication belong to the downstream process unless
explicitly included in the task. Report a downstream blocker without silently
expanding upstream work into downstream implementation.

## Maintain the knowledge base

Keep procedures here and dated facts in the observations reference, each with its
source and observation date. Replace superseded facts in place.

This skill is public. Record configuration and lifecycle facts only; keep traced
shipment contents, vulnerability or fix status, unannounced releases, customer
details, credentials and personal account identifiers out of it.
