"""GitOps PR automation — GitHub client, manifest editor, PR template."""

from pcap.gitops.github_client import GitHubClient, PRCreator
from pcap.gitops.manifest_editor import (
    ManifestEditor,
    ManifestFormat,
    detect_manifest_format,
)
from pcap.gitops.pr_template import render_pr_body, render_pr_title
from pcap.gitops.repo_layout import RepoLayout

__all__ = [
    "GitHubClient",
    "ManifestEditor",
    "ManifestFormat",
    "PRCreator",
    "RepoLayout",
    "detect_manifest_format",
    "render_pr_body",
    "render_pr_title",
]
