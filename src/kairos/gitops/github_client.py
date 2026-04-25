"""Async GitHub REST client — create branch, commit, open PR, label, request reviewers.

Uses `httpx` directly (not PyGithub) because PyGithub is synchronous. This client
is intentionally minimal: enough to open a PR for a single workload change.
"""

from __future__ import annotations

import base64
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import HttpUrl

from kairos.config.settings import GitHubSettings
from kairos.domain.enums import ScalingAction
from kairos.domain.exceptions import ExternalServiceError
from kairos.domain.models import LLMAdvice, PRResult, ScalingDecision
from kairos.gitops.manifest_editor import (
    ManifestEditor,
    ManifestFormat,
    detect_manifest_format,
)
from kairos.gitops.pr_template import render_pr_body, render_pr_title
from kairos.gitops.repo_layout import RepoLayout
from kairos.observability.metrics import EXTERNAL_CALL_DURATION, PRS_CREATED_TOTAL
from kairos.resilience.breakers import breaker_for
from kairos.storage.dedup import DedupStore

log = structlog.get_logger(__name__)

SERVICE = "github"


class GitHubClient:
    """Thin async wrapper over the GitHub REST API v3."""

    def __init__(
        self,
        settings: GitHubSettings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not settings.repo:
            raise ValueError("GitHub repo not configured")
        if settings.token is None:
            raise ValueError("GitHub token not configured")

        self._settings = settings
        self._breaker = breaker_for(SERVICE)

        self._client = client or httpx.AsyncClient(
            base_url=str(settings.api_url).rstrip("/"),
            timeout=httpx.Timeout(30.0),
            headers={
                "Authorization": f"Bearer {settings.token.get_secret_value()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── Low-level HTTP with breaker + metrics ─────────────────────────
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        start = time.perf_counter()
        result = "ok"

        async def _call() -> httpx.Response:
            r = await self._client.request(method, path, json=json, params=params)
            if r.status_code >= 400:
                raise ExternalServiceError(
                    SERVICE,
                    f"{method} {path} -> {r.status_code}: {r.text[:400]}",
                    status=r.status_code,
                )
            return r

        try:
            return await self._breaker.call(_call)
        except ExternalServiceError:
            result = "error"
            raise
        except httpx.HTTPError as exc:
            result = "http_error"
            raise ExternalServiceError(SERVICE, f"{type(exc).__name__}: {exc}") from exc
        finally:
            EXTERNAL_CALL_DURATION.labels(service=SERVICE, result=result).observe(
                time.perf_counter() - start
            )

    # ── Git data API ──────────────────────────────────────────────────
    async def get_branch_sha(self, branch: str) -> str:
        owner, repo = self._split_repo()
        r = await self._request("GET", f"/repos/{owner}/{repo}/branches/{branch}")
        return str(r.json()["commit"]["sha"])

    async def create_branch(self, new_branch: str, from_sha: str) -> None:
        owner, repo = self._split_repo()
        await self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{new_branch}", "sha": from_sha},
        )

    async def get_file(self, path: str, ref: str) -> tuple[str, str]:
        """Return (content, blob_sha)."""
        owner, repo = self._split_repo()
        r = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
        )
        j = r.json()
        content = base64.b64decode(j["content"]).decode("utf-8")
        return content, str(j["sha"])

    async def put_file(
        self,
        path: str,
        content: str,
        *,
        branch: str,
        message: str,
        sha: str,
    ) -> None:
        owner, repo = self._split_repo()
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        await self._request(
            "PUT",
            f"/repos/{owner}/{repo}/contents/{path}",
            json={
                "message": message,
                "content": encoded,
                "branch": branch,
                "sha": sha,
            },
        )

    async def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        owner, repo = self._split_repo()
        r = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
        )
        return dict(r.json())

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        if not labels:
            return
        owner, repo = self._split_repo()
        await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )

    async def request_reviewers(self, pr_number: int, reviewers: list[str]) -> None:
        if not reviewers:
            return
        owner, repo = self._split_repo()
        await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
            json={"reviewers": reviewers},
        )

    def _split_repo(self) -> tuple[str, str]:
        parts = self._settings.repo.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid repo format: {self._settings.repo!r}")
        return parts[0], parts[1]


class PRCreator:
    """Orchestrates: dedup → branch → edit file(s) → commit → PR → labels → reviewers."""

    def __init__(
        self,
        github: GitHubClient,
        dedup: DedupStore,
        layout: RepoLayout,
        *,
        github_settings: GitHubSettings,
        dry_run: bool = False,
    ) -> None:
        self._gh = github
        self._dedup = dedup
        self._layout = layout
        self._settings = github_settings
        self._dry_run = dry_run
        self._editor = ManifestEditor()

    async def create_pr_for_decision(
        self,
        decision: ScalingDecision,
        *,
        advice: LLMAdvice | None = None,
    ) -> PRResult | None:
        """Opens a PR for the decision (or returns None / dedup-hit result).

        Returns:
            * PRResult with real URL on success
            * PRResult(dry_run=True, ...) if dry-run mode
            * PRResult(dedup_hit=True, ...) if another PR exists for this decision_hash
            * None if action is NOOP / HUMAN_APPROVAL_REQUIRED (no PR should be opened)
        """
        if decision.action in (
            ScalingAction.NOOP,
            ScalingAction.HUMAN_APPROVAL_REQUIRED,
            ScalingAction.NODE_POOL_ADVISORY,  # handled via alerting, not PR
        ):
            return None

        first_sight = await self._dedup.first_sight_pr(decision)
        if not first_sight:
            PRS_CREATED_TOTAL.labels(result="dedup_hit").inc()
            return PRResult(
                url=HttpUrl("https://dedup.local/skipped"),
                number=0,
                branch="dedup",
                files_changed=[],
                dry_run=False,
                dedup_hit=True,
            )

        workload_dir = self._layout.workload_dir(decision.workload)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
        branch = self._layout.branch_name(decision.workload, stamp)
        base = self._settings.base_branch

        title = render_pr_title(decision)
        body = render_pr_body(decision, advice)
        files_changed: list[str] = []

        if self._dry_run:
            PRS_CREATED_TOTAL.labels(result="dry_run").inc()
            log.info(
                "pr_dry_run",
                branch=branch,
                workload=decision.workload.uid,
                action=decision.action.value,
                target_replicas=decision.target_replicas,
            )
            return PRResult(
                url=HttpUrl("https://dry-run.local/pr"),
                number=0,
                branch=branch,
                files_changed=[str(workload_dir / "deployment.yaml")],
                dry_run=True,
                dedup_hit=False,
            )

        # Create branch
        try:
            base_sha = await self._gh.get_branch_sha(base)
            await self._gh.create_branch(branch, base_sha)

            # Discover candidate manifest files
            for candidate in ("deployment.yaml", "values.yaml", "statefulset.yaml"):
                path = str(workload_dir / candidate)
                try:
                    content, blob_sha = await self._gh.get_file(path, ref=branch)
                except ExternalServiceError as exc:
                    if exc.status == 404:
                        continue
                    raise
                new_content = self._edit(decision, path, content)
                if new_content == content:
                    continue
                await self._gh.put_file(
                    path,
                    new_content,
                    branch=branch,
                    message=f"KAIROS: {decision.action.value} for "
                    f"{decision.workload.namespace}/{decision.workload.name}",
                    sha=blob_sha,
                )
                files_changed.append(path)

            if not files_changed:
                raise ExternalServiceError(
                    SERVICE,
                    f"no editable manifest found under {workload_dir}",
                )

            pr = await self._gh.create_pull_request(title=title, body=body, head=branch, base=base)
            pr_number = int(pr["number"])
            pr_url = HttpUrl(str(pr["html_url"]))

            labels = [
                *self._settings.labels,
                decision.action.value,
                f"severity:{decision.severity.value}",
            ]
            await self._gh.add_labels(pr_number, labels)
            reviewers = list(self._settings.reviewers)
            if reviewers:
                await self._gh.request_reviewers(pr_number, reviewers)

            PRS_CREATED_TOTAL.labels(result="created").inc()
            return PRResult(
                url=pr_url,
                number=pr_number,
                branch=branch,
                files_changed=files_changed,
                dry_run=False,
                dedup_hit=False,
            )
        except Exception:
            PRS_CREATED_TOTAL.labels(result="error").inc()
            raise

    def _edit(self, decision: ScalingDecision, path: str, content: str) -> str:
        fmt = detect_manifest_format(path, content)
        if fmt == ManifestFormat.KUSTOMIZE:
            out = content
            if decision.target_replicas is not None and decision.action in (
                ScalingAction.HORIZONTAL_UP,
                ScalingAction.HORIZONTAL_DOWN,
                ScalingAction.KEDA_PRESCALE,
            ):
                out = self._editor.set_replicas_kustomize(out, decision.target_replicas)
            if decision.target_cpu_request or decision.target_mem_request:
                out = self._editor.set_container_resources_kustomize(
                    out,
                    cpu_request=decision.target_cpu_request,
                    mem_request=decision.target_mem_request,
                )
            return out

        # Helm values
        out = content
        if decision.target_replicas is not None and decision.action in (
            ScalingAction.HORIZONTAL_UP,
            ScalingAction.HORIZONTAL_DOWN,
            ScalingAction.KEDA_PRESCALE,
        ):
            out = self._editor.set_replicas_helm(out, decision.target_replicas)
        if decision.target_cpu_request or decision.target_mem_request:
            out = self._editor.set_resources_helm(
                out,
                cpu_request=decision.target_cpu_request,
                mem_request=decision.target_mem_request,
            )
        return out
