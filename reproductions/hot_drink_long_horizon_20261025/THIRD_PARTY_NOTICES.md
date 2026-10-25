# Third-party sources in this experiment archive

The frozen runtime preserves its original bytes and upstream notices. The source identity is defined by `SOURCE_LINEAGE.json` and `STAGING_MANIFEST.json`. This archive does not relicense upstream software or grant rights to external simulator assets.

- BEHAVIOR-1K / OmniGibson: https://github.com/StanfordVL/BEHAVIOR-1K, base commit `b1979916ec1549b10a4e65e630bc6504a9af1b00`. Local modifications and untracked files are identified in `package_staging/upstream/provenance.json`. The license from that base is preserved in `third_party_licenses/OmniGibson.LICENSE`.
- BDDL: the same pinned repository's `bddl3/` subtree; license preserved in `third_party_licenses/BDDL.LICENSE`.
- The project's navigation attribution is preserved in `third_party_licenses/navigation.THIRD_PARTY_NOTICES.md` and the repository's root notice.
- BEHAVIOR object assets, robot assets, Isaac Sim and associated datasets retain their own terms. Large assets and license keys are not included; required external inputs are inventoried in `package_staging/asset_inventory/`.

The historical runs recorded the upstream commit but not the dirty-tree patch. The archived patch is a current recovery snapshot, with historical exactness explicitly unverified.
