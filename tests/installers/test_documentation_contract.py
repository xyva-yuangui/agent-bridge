"""Release-surface contracts kept close to the installer tests."""

from __future__ import annotations

import unittest
from pathlib import Path
import sys
import hashlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent_bridge.adapters import ADAPTER_TYPES
from agent_bridge.cli import build_parser


HOSTS = tuple(adapter.name for adapter in ADAPTER_TYPES)


class DocumentationContractTests(unittest.TestCase):
    def test_open_source_governance_and_maintainer_docs_exist(self) -> None:
        for relative in (
            "LICENSE",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "docs/architecture/v2.md",
            "docs/installation/windows.md",
            "docs/installation/macos.md",
            "docs/installation/migration-v1.md",
            "docs/release/checklist.md",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)

    def test_readmes_link_to_governance_and_state_honest_platform_support(self) -> None:
        for name in ("README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for link in ("LICENSE", "SECURITY.md", "CONTRIBUTING.md", "docs/installation/"):
                self.assertIn(link, text, name)
            for host in HOSTS:
                self.assertIn(host, text.lower(), name)
            self.assertIn("macOS", text, name)
            self.assertIn("real-machine", text.lower(), name)

    def test_documented_cli_commands_are_parser_commands(self) -> None:
        help_text = build_parser().format_help()
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for command in ("setup", "uninstall", "migrate", "export", "doctor", "tui"):
            self.assertIn(command, help_text)
            self.assertIn("bridge " + command, readme)

    def test_distribution_declares_all_host_templates_and_release_workflows(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.9"', pyproject)
        self.assertIn("integrations/*/*.json", pyproject)
        self.assertIn("integrations/*/*.py", pyproject)
        for host in HOSTS:
            self.assertTrue((ROOT / "src" / "agent_bridge" / "integrations" / host / "manifest.json").is_file(), host)
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for version in ("3.9", "3.10", "3.11", "3.12", "3.13"):
            self.assertIn(version, ci)
        for runner in ("windows-latest", "macos-latest", "ubuntu-latest"):
            self.assertIn(runner, ci)
        for token in ("sha256", "sbom", "notar", "attestation"):
            self.assertIn(token, release.lower())

    def test_sdist_manifest_carries_release_sources_and_docs(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for token in ("LICENSE", "SECURITY.md", "CONTRIBUTING.md", "docs", "native", "integrations"):
            self.assertIn(token, manifest)

    def test_zcode_uses_the_packaged_template_location(self) -> None:
        source = (ROOT / "src" / "agent_bridge" / "adapters" / "zcode.py").read_text(encoding="utf-8")
        self.assertIn('parents[1] / "integrations" / "zcode"', source)

    def test_release_docs_preserve_data_and_require_native_machine_evidence(self) -> None:
        docs = (ROOT / "docs" / "release" / "checklist.md").read_text(encoding="utf-8")
        migration = (ROOT / "docs" / "installation" / "migration-v1.md").read_text(encoding="utf-8")
        self.assertIn("preserves task data", migration)
        self.assertIn("Windows", docs)
        self.assertIn("macOS", docs)
        self.assertIn("real-machine", docs.lower())
        self.assertIn("notarization", docs.lower())

    def test_installer_docs_commit_to_one_all_host_auto_invocation_and_explicit_scope(self) -> None:
        windows = (ROOT / "docs" / "installation" / "windows.md").read_text(encoding="utf-8").lower()
        macos = (ROOT / "docs" / "installation" / "macos.md").read_text(encoding="utf-8").lower()
        for text, automatic, explicit in ((windows, "install.ps1 -auto", "-agent reasonix"), (macos, "install.sh --auto", "--agent reasonix")):
            self.assertIn("one", text)
            self.assertIn(automatic, text)
            self.assertIn(explicit, text)
            self.assertIn("degraded", text)
            for host in HOSTS:
                self.assertIn(host, text)

    def test_packaging_and_ci_do_not_depend_on_checkout_pythonpath(self) -> None:
        windows = (ROOT / "install.ps1").read_text(encoding="utf-8")
        shell = (ROOT / "install.sh").read_text(encoding="utf-8")
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("DevSourceFallback", windows)
        self.assertIn("DEGRADED development fallback", windows)
        self.assertIn("--dev-source-fallback", shell)
        self.assertEqual(shell.count('export PYTHONPATH="${source_root}/src'), 1)
        self.assertLess(shell.index("--dev-source-fallback"), shell.index('export PYTHONPATH="${source_root}/src'))
        for workflow in (ci, release):
            self.assertIn("actions/setup-python@v5", workflow)
            self.assertIn("--force-reinstall --no-deps", workflow)
        self.assertIn("verify-release.ps1", ci)
        self.assertIn("git cat-file -t", release)
        self.assertIn("native/windows-x86_64/*.exe", (ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_packaged_windows_helper_matches_the_verified_release_input(self) -> None:
        packaged = ROOT / "src" / "agent_bridge" / "native" / "windows-x86_64" / "agent-bridge-windows-notify.exe"
        verified = ROOT / "native" / "windows-notify" / "dist" / "windows-x86_64" / "agent-bridge-windows-notify.exe"
        self.assertTrue(packaged.is_file())
        self.assertEqual(hashlib.sha256(packaged.read_bytes()).hexdigest(), hashlib.sha256(verified.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
