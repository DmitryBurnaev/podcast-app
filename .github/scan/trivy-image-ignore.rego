package trivy

# Uncompressed Python 3.14.10 Alpine layer IDs verified on both service images.
python_base_layers := {
    "sha256:777bfdd276b1339ba90876c7b9bb0f4c04cdd38049a494d1250afef3ae17c392", # linux/amd64
    "sha256:e27bc4d9e2fd237f93910c7082a1e5117a8afc3f7ee41b624917c232af9c28c1", # linux/arm64
}

setuptools_sbom_findings := {"CVE-2025-47273", "CVE-2026-59890", "GHSA-xmj3-9gf7-h52w"}

ignore {
    input.Type == "vulnerability"
    input.PkgName == "setuptools"
    input.InstalledVersion == "70.3.0"
    input.PkgIdentifier.PURL == "pkg:pypi/setuptools@70.3.0"
    input.PkgIdentifier.BOMRef == "pkg:pypi/setuptools@70.3.0"
    object.get(input, "PkgPath", "") == ""
    input.Layer.DiffID == python_base_layers[_]
    input.VulnerabilityID == setuptools_sbom_findings[_]
}
