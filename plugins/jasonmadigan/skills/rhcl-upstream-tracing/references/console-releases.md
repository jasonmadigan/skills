# Console-plugin release planning

## Identify the streams

Read the relevant product-build branch's submodules and operator image-selection
code. RHCL can ship several plugin images in one release. The PF5, SDK1 and latest
suffixes name compatibility tiers; they are not RHCL version numbers.

```sh
gh api repos/rh-api-management/rhcl-console-plugin-product-build/branches --paginate
gh api repos/rh-api-management/rhcl-console-plugin-product-build/git/trees/rhcl-1.5 \
  --jq '.tree[] | select(.type == "commit") | {path,sha}'
gh api repos/Kuadrant/kuadrant-console-plugin/branches --paginate
gh api repos/Kuadrant/kuadrant-console-plugin/releases --paginate
```

Use the operator's selected release branch for `release.yaml` and image selection.
Development `main` may contain placeholder dependency versions. A single
`dependencies.console-plugin` value may omit older compatibility images; inspect
bundle environment variables and `relatedImages` as well.

Intersect the operator's selection rules with RHCL's supported OCP matrix. When a
user excludes a stream, respect that scope and describe any remaining coverage
separately; do not add its release back to the execution plan.

## Produce a concrete plan

For each selected branch record:

- Latest published tag and its peeled SHA; current branch SHA; next unused tag.
- Included fix PRs and every additional change since that tag.
- Version-field changes, required checks and any branch-specific verification.
- Publication workflow at the candidate commit, its trigger and secret names.
- Remaining decisions and downstream handoff evidence.

Inspect tag and Quay state as well as GitHub releases; any can exist independently.
Compare the exact commits, not GitHub release `target_commitish` alone.

An existing registry tag blocks treating a version as unused, even when the Git
tag and GitHub Release are absent. Inspect its digest, provenance and workflow
behaviour before triggering a build. Replacing an existing image needs explicit
recovery authorisation; propose an unused version instead of silently substituting
one or overwriting the artifact.

```sh
gh api repos/Kuadrant/kuadrant-console-plugin/compare/v0.7.1...release-0.7
gh api repos/Kuadrant/kuadrant-console-plugin/git/ref/tags/v0.7.1
```

For annotated tags, resolve the tag object through the Git tags API until a commit
is reached. Compare a fix commit to the candidate commit: `ahead` or `identical`
proves ancestry; `diverged` does not. Squash merges and cherry-picks need content or
patch-equivalence checks when ancestry differs.

## Preparation and verification

Read the current repository `RELEASE.md` and release command before execution.
Some versions assume `main` throughout. Patch preparation must target its own
`release-X.Y` branch; a development `main` cannot substitute for that branch.

Prepare a reviewed release PR setting both `package.json.version` and
`consolePlugin.version` to the selected stable version. Historical tags may have
left these fields stale; choose the next version from published tags and current
release intent. Check lockfile/workspace consistency rather than upgrading packages
as part of the version bump. Release branches normally receive no follow-up `-dev`
bump. Treat suffix tags such as `v0.1.5-2` according to their established convention.

Run the repository checks appropriate to the branch and inspect PR CI at its
current head. Some older branches lack unit or e2e workflows; report the actual
coverage instead of treating absent checks as successes. Exercise changed runtime
behaviour: a passing build does not validate Module Federation in a real Console.
For a dependency change, distinguish build-only packages from shipped modules and
inspect the affected runtime flow when necessary.

## Publication, when authorised

Tag the reviewed release PR merge commit, after refreshing its SHA and required
checks. Use a signed annotated tag and push only that exact tag ref. Preserve
published tags; resolve a conflict with a new version or an agreed recovery.

The workflow from the tagged commit determines what runs. The current modern
workflow is `build-push.yaml`, triggered by tag push, using `IMG_REGISTRY_USERNAME`
and `IMG_REGISTRY_TOKEN`. Verify the selected branch rather than assuming main's
workflow applies. Older PF5 workflows have used release-publication events and
`QUAY_USER`/`QUAY_PASSWORD`; inspect current secret names and repository access
without reading secret values.

Create the GitHub Release against the verified existing tag. Generate notes from
the preceding tag in that same stream and check included changes. Choose GitHub's
“Latest” designation deliberately so an older maintenance release does not replace
the preferred stable stream. Keep release notes factual and omit private trackers.

Verify the workflow for the exact tag and SHA, then the Quay manifest digest,
expected architectures and expiry. A GitHub Release alone does not prove that an
image was published. Distinguish a successful branch image from the stable tag.

Hand over upstream tags, commits, image digests and any downstream blocker. A
product-build pin update and RHCL release are separate work. Update public docs or
operator pins only within the authorised scope; retain Jira targets independently
of upstream tag numbering.
