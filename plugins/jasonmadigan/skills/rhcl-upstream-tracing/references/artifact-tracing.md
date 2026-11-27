# Trace a fix into a released RHCL image

## Evidence chain

```text
RHCL release / published erratum
  -> released operator bundle digest
  -> related component image digest, for each selected compatibility tier
  -> component build provenance or product-build commit
  -> upstream submodule commit
  -> fix commit or verified equivalent patch
```

Branch pins answer what source is configured now. A successful released build may
use an older pin. Record each edge and stop at the first unproven link when making
a shipping claim.

## Prefer catalog metadata

The Red Hat catalog endpoints in [sources and Jira](sources-and-jira.md) expose
bundles and component images without requiring a registry pull. Follow pagination
and select the actual product version. Enumerate all console-plugin related images,
including older compatibility tiers.

Match a related image's exact manifest digest to its catalog image record. Inspect
labels and provenance. `vcs-ref` commonly names the product-build repository commit,
not an upstream Kuadrant commit. Read that commit's submodule tree, rather than the
current product-build branch. Confirm upstream inclusion by ancestry or a content
comparison for backports.

Use SBOM/provenance and package contents for dependency versions. Runtime module
names or binary strings can corroborate a feature, but do not by themselves prove
a specific security fix. A build-time dependency may appear in an SBOM even when
absent from the running image; report those as separate facts.

An npm SBOM from a Hermeto prefetch lists every lockfile entry, build-only packages
included, each with `hermeto:found_by=hermeto`. Settle presence from the layer's file
list and file contents. In Claude Code's
Bash tool `grep` can be a ugrep wrapper with `--ignore-files -I`, and the RHDH plugin
directories ship a `.gitignore` of `*`, so a recursive search over extracted content
silently finds nothing; use `/usr/bin/grep`.

## Registry fallback

When catalog metadata leaves a gap, use an existing authorised registry credential
to inspect the digest. Discover available `oc`, `skopeo`, `crane` or Docker tools
before prescribing a pull workflow. Request a pull secret only when necessary;
never display or persist its contents in the report.

For an OCP operator index, extract `/configs/rhcl-operator/` and locate the desired
bundle entry. FBC files may contain concatenated JSON objects rather than one JSON
array. Parse them with `JSONDecoder.raw_decode`, advancing over whitespace:

```python
import json

def fbc_objects(text):
    decoder = json.JSONDecoder()
    offset = 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        item, offset = decoder.raw_decode(text, offset)
        yield item
```

Extract the selected bundle's CSV under `/manifests/`, then inspect its
`relatedImages` and configured `RELATED_IMAGE_*` environment values. Follow
component image digests, not just the operator's `containerImage` annotation.
Temporary extraction containers need unique names and cleanup after inspection.

For a running cluster, also record the installed CSV, plugin Deployment image and
pod imageID. Cluster state may lag a released bundle, and an OCP upgrade may retain
the earlier compatible plugin until the upgrade completes.

## Report the strongest supported conclusion

Use precise states: merged upstream, tagged upstream, image built, selected by the
product-build branch, or shipped in RHCL. Include RHCL version, component version,
image digest, upstream SHA, source links and any missing edge. A support-policy
claim needs lifecycle evidence, not a successful artifact trace.
