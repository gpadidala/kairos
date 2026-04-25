"""GitOps PR automation — GitHub client, manifest editor, PR template."""

from kairos.gitops.github_client import GitHubClient, PRCreator
from kairos.gitops.manifest_editor import (
    ManifestEditor,
    ManifestFormat,
    detect_manifest_format,
)
from kairos.gitops.pr_template import render_pr_body, render_pr_title
from kairos.gitops.repo_layout import RepoLayout

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
