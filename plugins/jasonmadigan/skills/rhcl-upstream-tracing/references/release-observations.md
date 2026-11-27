# Release observations

An unofficial, dated snapshot of configuration and lifecycle facts read from the
public sources linked here; not a Red Hat statement. Refresh live sources before
selecting versions or making support or shipment claims, and establish what a
release shipped with [artifact tracing](artifact-tracing.md). It records facts, not
permission to release.

Read 27 November 2026 unless a row says otherwise.

## RHCL streams and console plugin tiers

The operator picks a console plugin image by OpenShift version in
`internal/openshift/utils.go`; product-build branches pin the source of each tier.
These are configured relationships, not proof of what a release shipped.

| RHCL stream | Operator branch | Console plugin tier by OpenShift version |
|-|-|-|
| 1.3 | `release-1.4` | latest (`release-0.3`) on 4.20 and later, PF5 (`release-v0.1.5`) below; rules read 22 October 2026 |
| 1.4 | `release-1.5` | latest (`release-0.4`) on 4.20 and later, PF5 (`release-v0.1.5`) below |
| 1.5 | `release-1.6` | latest (`release-0.7`) on 4.22 and later, SDK1 (`release-0.6`) on 4.20 and 4.21, PF5 (`release-v0.1.5`) below |

With the supported OpenShift versions under [Lifecycle](#lifecycle), PF5 runs on
RHCL 1.4 only with OpenShift 4.19, and no supported RHCL 1.5 cluster selects it.

Sources: product-build branches
[1.3](https://github.com/rh-api-management/rhcl-console-plugin-product-build/tree/rhcl-1.3),
[1.4](https://github.com/rh-api-management/rhcl-console-plugin-product-build/tree/rhcl-1.4),
[1.5](https://github.com/rh-api-management/rhcl-console-plugin-product-build/tree/rhcl-1.5);
operator release configurations
[1.4](https://github.com/Kuadrant/kuadrant-operator/blob/release-1.4/release.yaml),
[1.5](https://github.com/Kuadrant/kuadrant-operator/blob/release-1.5/release.yaml),
[1.6](https://github.com/Kuadrant/kuadrant-operator/blob/release-1.6/release.yaml),
and [1.6 image selection](https://github.com/Kuadrant/kuadrant-operator/blob/release-1.6/internal/openshift/utils.go).

## Lifecycle

The [lifecycle API](https://access.redhat.com/product-life-cycles/api/v1/products?name=Red%20Hat%20Connectivity%20Link)
on 27 November 2026:

| RHCL | Phase | GA | Full support ends | Maintenance ends | OpenShift |
|-|-|-|-|-|-|
| 1.5 | Full Support | 22 October 2026 | 31 August 2027 | 31 December 2027 | 4.20, 4.21, 4.22, 5.0 |
| 1.4 | Maintenance Support | 11 June 2026 | 31 October 2026 | 27 February 2027 | 4.19, 4.20, 4.21, 4.22 |
| 1.3 | End of life | 26 February 2026 | 11 June 2026 | 31 October 2026 | 4.18, 4.19, 4.20, 4.21 |

What each phase covers is set by the
[lifecycle policy](https://access.redhat.com/support/policy/updates/red-hat-connectivity-link).

## Upstream console-plugin release branches

GitHub began moving `ubuntu-latest` to Ubuntu 26.04 on 19 October 2026. Playwright
1.58.2, resolved from `release-0.6`'s `^1.50.0`, fails there with `Playwright does
not support chromium on ubuntu26.04-x64`. `release-0.4` and `release-0.6` have pinned
their e2e runner to `ubuntu-24.04` since 10 November 2026 (#1006, #1005).

Since #869 merged on 23 October 2026, `RELEASE.md` and `/release` handle
`release-X.Y` patches. On that date `release-0.3` and `release-0.4` lacked `unit` and
`e2e-smoke / e2e-rbac` (`release-0.4` had only an older `e2e-rbac` job), and
`release-v0.1.5` used `release-build.yaml` while the other four used
`build-push.yaml`.
