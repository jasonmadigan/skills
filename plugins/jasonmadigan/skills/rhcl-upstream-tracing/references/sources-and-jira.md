# Sources and Jira

## Which source answers which question?

| Question | Source | Limit |
|-|-|-|
| Which fixVersions can Jira accept? | Authenticated CONNLINK project versions and issue edit metadata | Availability and released flags can lag events |
| What release is planned? | Release epic, schedule and explicit maintainer decision | A target is not shipment evidence |
| What is supported? | Red Hat lifecycle dates API and lifecycle policy | Dates and policy can disagree; expose the discrepancy |
| What upstream source was released? | Git tag peeled to commit, GitHub release, release workflow | Release metadata target_commitish can be stale |
| Which source will a product build use? | Product-build submodules at a recorded commit | Branch tips do not prove a successful released build |
| What shipped? | Released operator bundle and related image digests, provenance/SBOM | Image labels may identify midstream rather than upstream |
| Which plugin runs on an OCP version? | Operator image-selection code and the bundle's configured images | Compatibility is distinct from the RHCL support matrix |

## Authoritative endpoints

- [RHCL lifecycle policy](https://access.redhat.com/support/policy/updates/red-hat-connectivity-link).
- [Lifecycle dates API](https://access.redhat.com/product-life-cycles/api/v1/products?name=Red%20Hat%20Connectivity%20Link).
  Read `data[].versions[]`: `type`, phase dates and `openshift_compatibility`.
- [RHCL product-build repository](https://github.com/rh-api-management/rhcl-console-plugin-product-build).
- [Kuadrant operator](https://github.com/Kuadrant/kuadrant-operator): `release.yaml`,
  `internal/openshift/utils.go`, and the selected bundle's CSV/related images.
- [Console-plugin releases](https://github.com/Kuadrant/kuadrant-console-plugin/releases).
- [Quay image tags](https://quay.io/repository/kuadrant/console-plugin?tab=tags).
- [RHCL operator bundles](https://catalog.redhat.com/api/containers/v1/operators/bundles?filter=package%3D%3Drhcl-operator).
- [Downstream console images](https://catalog.redhat.com/api/containers/v1/repositories/registry/registry.access.redhat.com/repository/rhcl-1/rhcl-console-plugin-rhel9/images).

Follow API pagination: the catalog bundle list counts pages from `page=0` and returns
at most 500 items, so compare `total` with the items received. Save retrieval time
and immutable references when reporting
what shipped. A future date in the lifecycle API does not prove GA happened.
Use published errata and released catalog bundles to check availability.

## Jira access

Prefer an authenticated Jira connector or the configured `mcp-atlassian` server.
If only REST is available, reuse the configured Jira URL, username and API token
through the process environment. Validate `GET /rest/api/3/myself` first. Jira can
return public search results anonymously when Basic authentication is invalid;
public results alone do not validate credentials. Keep tokens out of arguments,
logs, files and responses.

Resolve live metadata through these Jira Cloud endpoints:

```text
GET /rest/api/3/project/CONNLINK/versions
GET /rest/api/3/field
GET /rest/api/3/issue/{key}?expand=names,editmeta
GET /rest/api/3/issue/{key}/transitions
GET /rest/api/3/issue/{key}/remotelink
GET /rest/api/3/search/jql
```

For unreleased versions, report exact names and IDs where needed, separating
archived entries. Reconcile the version's `released` flag with the release epic
and errata before calling a version upcoming. Search/jql pagination uses
`nextPageToken` and `isLast`; do not assume the first page is complete.

## Choosing and changing fixVersions

A CONNLINK fixVersion names an RHCL product release. Determine whether the user is
setting a future target or recording an already shipped fix. The first requires
a target decision; the second requires downstream artifact evidence.

Resolve the requested release to an existing version ID. If Jira abbreviates a
version, disclose the exact stored name. Never create or rename a project version
merely to normalise spelling. When the user chooses a later release than the
issue's affected version, keep the affected version and say so. An unset target is
a valid decision.

For mutations, use the live operations from edit metadata. Use `add` to preserve
existing multi-value fields, or reread and merge before `set`. Transition by the
live destination status: a user may say “In Review” while Jira calls it “Review”.
Read back status, fixVersions and resolution after the change.

Assignee is single-user. For “add me as well”, inspect editable multi-user fields
such as Contributors or Additional Assignees; preserve the existing owner.

Link the exact component PRs supported by package and branch evidence. A main PR
can be an upstream source reference, but does not establish release-branch or
RHCL inclusion. Check existing remote links before adding duplicates. Preserve
tracker visibility and keep restricted tracker details out of public release notes.
