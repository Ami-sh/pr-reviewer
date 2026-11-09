"""The review pipeline: fetch → checkout → understand → find → verify → synthesize.

One-shot by design: the output is a Review saved under local/<id>/ for a human
to edit and post.  See DESIGN.md for the rationale behind each stage.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from . import log, prompts
from .backends import Backend, LLMResult, make_backend
from .config import Settings
from .context import build_context_pack, pr_header
from .diff import ParsedDiff, parse_unified_diff
from .github import GitHubClient
from .ids import make_review_id
from .repo import RepoCheckout
from .schema import (
    FINDINGS_SCHEMA,
    SEVERITIES,
    SEVERITY_RANK,
    SYNTHESIS_SCHEMA,
    UNDERSTANDING_SCHEMA,
    VERIFY_SCHEMA,
    Finding,
    PRInfo,
    Review,
    Understanding,
)
from .store import ReviewStore, now_iso
from .urls import parse_pr_url

MAX_FINDINGS_PER_LENS = {"low": 6, "medium": 10, "high": 15}

CRITIC_GUIDANCE = {
    "low": "Report only problems of severity medium or above that you are confident about (confidence >= 0.6). Skip nits, style and speculative concerns. Time-box exploration.",
    "medium": "Report medium/high/critical problems you verified, plus low-severity ones only when the fix is cheap and clear. No nits unless the repository conventions demand them.",
    "high": "Be exhaustive: every real problem down to low severity, and nits where they reflect repository conventions or would mislead future readers. Explore callers and tests thoroughly before concluding something is fine. Use the full exploration budget.",
}


class ReviewRun:
    """Holds state for one review run."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = ReviewStore(settings.local_dir)
        self.stats: Dict[str, Any] = {"cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "stages": {}}
        self.backend: Optional[Backend] = None
        self.rid: Optional[str] = None
        self.pr: Optional[PRInfo] = None
        self.diff: Optional[ParsedDiff] = None
        self.repo: Optional[RepoCheckout] = None
        self.pack: Dict[str, str] = {}

    # ---------------------------------------------------------------- utils
    def _account(self, stage: str, res: LLMResult) -> None:
        self.stats["cost_usd"] = round(self.stats["cost_usd"] + (res.cost_usd or 0.0), 4)
        self.stats["calls"] += 1
        self.stats["input_tokens"] += res.input_tokens
        self.stats["output_tokens"] += res.output_tokens
        st = self.stats["stages"].setdefault(stage, {"calls": 0, "cost_usd": 0.0, "seconds": 0.0, "errors": 0})
        st["calls"] += 1
        st["cost_usd"] = round(st["cost_usd"] + (res.cost_usd or 0.0), 4)
        st["seconds"] = round(st["seconds"] + res.duration_s, 1)
        if not res.ok:
            st["errors"] += 1

    def _call(self, stage: str, label: str, prompt: str, schema: Dict[str, Any], attempts: int = 2) -> LLMResult:
        assert self.backend and self.repo
        system = prompts.render(prompts.load("system"), base_ref=self.pr.base_ref if self.pr else "main")
        last: Optional[LLMResult] = None
        for i in range(attempts):
            res = self.backend.run(stage=stage, system=system, prompt=prompt, schema=schema, cwd=self.repo.worktree, label=label)
            self._account(stage, res)
            if res.ok:
                return res
            last = res
            log.warn(f"[{label}] attempt {i + 1} failed: {res.error}")
        return last  # type: ignore[return-value]

    def _user_prompt_block(self) -> str:
        up = (self.settings.user_prompt or "").strip()
        if not up:
            return ""
        return "## Instructions from the human requesting this review (highest priority)\n<reviewer_instructions>\n" + up + "\n</reviewer_instructions>\n"

    # ---------------------------------------------------------------- stage 0
    def fetch(self) -> None:
        s = self.settings
        ref = parse_pr_url(s.pr_url)
        client = GitHubClient(ref)
        log.info(f"fetching {ref.full_name}#{ref.number} ...")
        self.pr = client.fetch_pr()
        self.rid = make_review_id(self.pr.repo, self.pr.number, s.local_dir)
        self.store.create(self.rid)
        self.store.init_meta(self.rid, self.pr.url, self._settings_dict())
        self.store.write_json(self.rid, "pr.json", self.pr.to_dict())
        diff_text = client.fetch_diff()
        self.store.write_text(self.rid, "diff.patch", diff_text)
        self.diff = parse_unified_diff(diff_text)
        self.store.write_json(self.rid, "diff.json", self.diff.to_dict())
        log.info(f"PR: {self.pr.title!r} by {self.pr.author} — {self.diff.stats()}")
        self.repo = RepoCheckout(self.pr, s.local_dir, self.rid, token=client.token)
        self.repo.prepare()
        if not self.diff.files:
            # GitHub returned nothing usable (e.g. huge PR): try the local three-dot diff
            local = self.repo.local_diff()
            if local:
                self.store.write_text(self.rid, "diff.patch", local)
                self.diff = parse_unified_diff(local)
                self.store.write_json(self.rid, "diff.json", self.diff.to_dict())
        self.backend = make_backend(s, call_logger=lambda name, payload: self.store.save_call(self.rid, name, payload))
        log.info(f"backend: {self.backend.describe()}  critic={s.critic_level}  lenses={self.settings.profile.lenses}")
        self.pack = build_context_pack(self.pr, self.diff, self.repo, depth=s.profile.understand_depth, user_prompt=s.user_prompt)

    def _settings_dict(self) -> Dict[str, Any]:
        s = self.settings
        method = s.resolve_method()
        return {
            "method": method,
            "model": s.resolve_model(method),
            "provider": s.resolve_provider() if method == "api" else None,
            "critic_level": s.critic_level,
            "user_prompt": s.user_prompt,
            "allow_exec": s.allow_exec,
            "lenses": s.profile.lenses,
            "verify_votes": s.profile.verify_votes,
        }

    # ---------------------------------------------------------------- stage 1
    def understand(self) -> Understanding:
        log.info("stage 1/4: understanding the repository and the change ...")
        prompt = prompts.render(
            prompts.load("understand"),
            user_prompt_block=self._user_prompt_block(),
            depth=self.settings.profile.understand_depth,
            **self.pack,
        )
        res = self._call("understand", "understand", prompt, UNDERSTANDING_SCHEMA)
        if res.ok:
            u = Understanding.from_dict(res.data)
        else:
            log.warn("understand stage failed; continuing with a minimal understanding")
            u = Understanding("(understand stage failed)", [], "", self.pr.body[:2000] if self.pr else "", "", [], [], "")
        self.store.write_json(self.rid, "understanding.json", u.to_dict())
        return u

    # ---------------------------------------------------------------- stage 2
    def find(self, understanding: Understanding) -> Tuple[List[Finding], List[str], List[str]]:
        prof = self.settings.profile
        log.info(f"stage 2/4: {len(prof.lenses)} reviewer lenses in parallel ({self.settings.parallel} at a time) ...")
        base = dict(self.pack)
        base["understanding"] = understanding.to_json()
        base["user_prompt_block"] = self._user_prompt_block()
        base["critic_level"] = prof.name
        base["critic_guidance"] = CRITIC_GUIDANCE[prof.name]
        base["max_findings"] = MAX_FINDINGS_PER_LENS[prof.name]
        base["base_ref"] = self.pr.base_ref

        def run_lens(lens: str) -> Tuple[str, LLMResult]:
            charter = prompts.render(prompts.lens_charter(lens), base_ref=self.pr.base_ref)
            prompt = prompts.render(prompts.load("find"), lens=lens, charter=charter, **base)
            return lens, self._call("find", f"find-{lens}", prompt, FINDINGS_SCHEMA)

        findings: List[Finding] = []
        good: List[str] = []
        coverage: List[str] = []
        raw_dump: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=max(1, self.settings.parallel)) as ex:
            futs = [ex.submit(run_lens, lens) for lens in prof.lenses]
            for fut in as_completed(futs):
                lens, res = fut.result()
                if not res.ok:
                    log.warn(f"lens {lens} produced no result: {res.error}")
                    raw_dump[lens] = {"error": res.error}
                    continue
                data = res.data
                raw_dump[lens] = data
                for i, fd in enumerate(data.get("findings") or []):
                    try:
                        f = self._normalise_finding(fd, lens, i)
                    except Exception as e:  # never let one bad item kill the run
                        log.debug(f"skipping malformed finding from {lens}: {e}")
                        continue
                    if f is not None:
                        findings.append(f)
                good.extend(x for x in (data.get("what_is_good") or []) if isinstance(x, str))
                if data.get("coverage_notes"):
                    coverage.append(f"[{lens}] {data['coverage_notes']}")
                log.info(f"  lens {lens}: {len(data.get('findings') or [])} finding(s)")
        self.store.write_json(self.rid, "findings_raw.json", {"lenses": raw_dump, "normalised": [f.to_dict() for f in findings]})
        before = len(findings)
        findings = dedupe(findings)
        if len(findings) != before:
            log.info(f"  deduplicated {before} -> {len(findings)}")
        return findings, good, coverage

    def _normalise_finding(self, fd: Dict[str, Any], lens: str, i: int) -> Optional[Finding]:
        fd = dict(fd)
        fd["id"] = f"{lens}-{i + 1}"
        fd["lens"] = lens
        fd["severity"] = str(fd.get("severity", "medium")).lower()
        if fd["severity"] not in SEVERITIES:
            fd["severity"] = "medium"
        fd["category"] = str(fd.get("category", "bug")).lower()
        fd["side"] = str(fd.get("side") or "RIGHT").upper()
        if fd["side"] not in ("LEFT", "RIGHT"):
            fd["side"] = "RIGHT"
        try:
            fd["confidence"] = max(0.0, min(1.0, float(fd.get("confidence", 0.5))))
        except (TypeError, ValueError):
            fd["confidence"] = 0.5
        for k in ("line", "start_line"):
            v = fd.get(k)
            if isinstance(v, str) and v.strip().lstrip("LR").isdigit():
                v = int(v.strip().lstrip("LR"))
            fd[k] = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        fd["path"] = _match_path(str(fd.get("path") or ""), self.diff.paths())
        for k in ("title", "evidence", "explanation", "suggestion"):
            fd[k] = str(fd.get(k) or "")
        if not fd["title"]:
            return None
        if fd.get("suggested_code") in ("", "null"):
            fd["suggested_code"] = None
        f = Finding.from_dict(fd)
        # snap to a commentable line now so the verifier sees the real anchor
        if f.line is not None and f.path:
            snapped = self.diff.snap(f.path, f.side, f.line, radius=5)
            if snapped:
                f.side, f.line = snapped
        if f.start_line is not None and (f.line is None or f.start_line >= f.line):
            f.start_line = None
        return f

    # ---------------------------------------------------------------- stage 3
    def verify(self, findings: List[Finding], understanding: Understanding) -> List[Finding]:
        prof = self.settings.profile
        votes = prof.verify_votes
        if votes <= 0 or not findings:
            log.info("stage 3/4: verification skipped at this critic level" if votes <= 0 else "stage 3/4: nothing to verify")
            for f in findings:
                f.verification = {"verdict": "unverified", "votes": 0, "confidence": f.confidence}
            return self._threshold(findings)
        log.info(f"stage 3/4: adversarial verification of {len(findings)} finding(s) × {votes} vote(s) ...")
        u_short = json.dumps({k: understanding.to_dict()[k] for k in ("repo_summary", "conventions", "pr_claims", "change_summary")}, ensure_ascii=False)
        header = self.pack["pr_header"]

        def run_vote(f: Finding, k: int) -> Tuple[str, int, LLMResult]:
            fd = {kk: vv for kk, vv in f.to_dict().items() if kk in ("title", "severity", "category", "path", "side", "line", "start_line", "evidence", "explanation", "suggestion")}
            snippet = self.diff.context_snippet(f.path, f.side, f.line, 6, 6) if f.line else "(file-level finding)"
            file_diff = self.diff.annotate([f.path], max_chars=30_000) if f.path else ""
            prompt = prompts.render(
                prompts.load("verify"),
                finding=json.dumps(fd, indent=2, ensure_ascii=False),
                snippet=snippet,
                path=f.path,
                file_diff=file_diff,
                understanding_short=u_short,
                user_prompt_block=self._user_prompt_block(),
                base_ref=self.pr.base_ref,
                pr_header=header,
            )
            return f.id, k, self._call("verify", f"verify-{f.id}-v{k + 1}", prompt, VERIFY_SCHEMA, attempts=1)

        results: Dict[str, List[Dict[str, Any]]] = {f.id: [] for f in findings}
        with ThreadPoolExecutor(max_workers=max(1, self.settings.parallel)) as ex:
            futs = [ex.submit(run_vote, f, k) for f in findings for k in range(votes)]
            for fut in as_completed(futs):
                fid, k, res = fut.result()
                if res.ok:
                    results[fid].append(res.data)
                else:
                    results[fid].append({"verdict": "uncertain", "confidence": 0.5, "reasoning": f"verifier error: {res.error}", "error": True})
        self.store.write_json(self.rid, "verification.json", results)

        kept: List[Finding] = []
        for f in findings:
            vs = results.get(f.id, [])
            summary = aggregate_votes(vs, f.confidence)
            f.verification = summary
            apply_corrections(f, vs, self.diff)
            f.confidence = summary["confidence"]
            if summary["verdict"] == "refuted":
                log.info(f"  refuted: [{f.severity}] {f.title}  ({summary['tally']})")
                continue
            kept.append(f)
        log.info(f"  {len(kept)}/{len(findings)} finding(s) survived verification")
        return self._threshold(kept)

    def _threshold(self, findings: List[Finding]) -> List[Finding]:
        prof = self.settings.profile
        out: List[Finding] = []
        for f in findings:
            if SEVERITY_RANK[f.severity] > SEVERITY_RANK[prof.min_severity]:
                continue
            if f.confidence < prof.min_confidence and f.severity not in ("critical",):
                log.info(f"  dropped (confidence {f.confidence:.2f} < {prof.min_confidence}): {f.title}")
                continue
            out.append(f)
        return out

    # ---------------------------------------------------------------- stage 4
    def synthesize(self, findings: List[Finding], understanding: Understanding, good: List[str], coverage: List[str]) -> Review:
        log.info("stage 4/4: synthesizing the review ...")
        prof = self.settings.profile
        fin_in = [
            {
                "id": f.id,
                "lens": f.lens,
                "severity": f.severity,
                "category": f.category,
                "confidence": round(f.confidence, 2),
                "path": f.path,
                "side": f.side,
                "line": f.line,
                "start_line": f.start_line,
                "title": f.title,
                "evidence": f.evidence[:1500],
                "explanation": f.explanation[:3000],
                "suggestion": f.suggestion[:1500],
                "has_suggested_code": bool(f.suggested_code),
                "verification": {k: f.verification.get(k) for k in ("verdict", "votes", "confidence", "tally", "reasoning") if k in f.verification},
            }
            for f in findings
        ]
        verdict_extra = ""
        if self.pr and self.pr.draft:
            verdict_extra = "This is a DRAFT PR: prefer COMMENT over REQUEST_CHANGES unless something is critical."
        prompt = prompts.render(
            prompts.load("synthesize"),
            user_prompt_block=self._user_prompt_block(),
            pr_header_short="\n".join(self.pack["pr_header"].splitlines()[:6]),
            understanding=understanding.to_json(),
            findings=json.dumps(fin_in, indent=2, ensure_ascii=False) if fin_in else "(no findings survived verification)",
            what_is_good="\n".join(f"- {g}" for g in dict.fromkeys(good)) or "(none recorded)",
            coverage_notes="\n".join(coverage) or "(none)",
            critic_level=prof.name,
            verdict_extra=verdict_extra,
        )
        res = self._call("synthesize", "synthesize", prompt, SYNTHESIS_SCHEMA)
        by_id = {f.id: f for f in findings}
        if res.ok:
            data = res.data
            verdict = data.get("verdict") or "COMMENT"
            summary = data.get("summary_markdown") or ""
            keep_ids: List[str] = []
            for item in data.get("findings") or []:
                fid = item.get("id")
                f = by_id.get(fid)
                if not f:
                    continue
                if not item.get("keep", True):
                    f.status = "cut"
                    f.verification["synth_note"] = f"dropped by synthesis" + (f" (merged into {item.get('merged_into')})" if item.get("merged_into") else "")
                    continue
                if item.get("severity") in SEVERITIES:
                    f.severity = item["severity"]
                if item.get("body"):
                    f.body = str(item["body"]).strip()
                keep_ids.append(fid)
            # findings the synthesizer forgot to mention stay active with their explanation as body
            for f in findings:
                if f.status == "active" and not f.body:
                    f.body = f.explanation.strip()
        else:
            log.warn("synthesis failed; building a plain summary")
            verdict, summary = fallback_summary(findings, understanding, good)
            for f in findings:
                f.body = f.body or f.explanation.strip()
        verdict = enforce_verdict(verdict, [f for f in findings if f.status == "active"], draft=bool(self.pr and self.pr.draft))
        review = Review(
            id=self.rid,
            created_at=now_iso(),
            pr=self._pr_subset(),
            settings=self._settings_dict(),
            verdict=verdict,
            summary=summary.strip(),
            findings=sorted(findings, key=lambda f: (f.status != "active", f.severity_rank(), f.path, f.line or 0)),
            understanding=understanding.to_dict(),
            stats=self.stats,
        )
        return review

    def _pr_subset(self) -> Dict[str, Any]:
        p = self.pr
        return {
            "host": p.host,
            "owner": p.owner,
            "repo": p.repo,
            "number": p.number,
            "url": p.url,
            "title": p.title,
            "author": p.author,
            "draft": p.draft,
            "base_ref": p.base_ref,
            "head_ref": p.head_ref,
            "head_sha": p.head_sha,
            "base_sha": p.base_sha,
        }

    # ------------------------------------------------------------------ run
    def run(self, keep_repo: bool = False) -> Review:
        t0 = time.time()
        self.fetch()
        try:
            understanding = self.understand()
            findings, good, coverage = self.find(understanding)
            findings = self.verify(findings, understanding)
            review = self.synthesize(findings, understanding, good, coverage)
            self.stats["seconds_total"] = round(time.time() - t0, 1)
            review.stats = self.stats
            self.store.save_review(review, original=True)
            from .render import write_review_html

            write_review_html(self.store, self.rid, review, self.store.read_json(self.rid, "diff.json"), served=False)
            self.store.set_status(self.rid, "draft", created_at=self.store.read_json(self.rid, "meta.json", {}).get("created_at"), title=self.pr.title, pr_url=self.pr.url)
            return review
        except Exception:
            self.store.set_status(self.rid, "failed")
            raise
        finally:
            if self.repo and not keep_repo:
                try:
                    self.repo.remove_worktree()
                except Exception as e:  # pragma: no cover
                    log.debug(f"worktree cleanup failed: {e}")


def run_review(settings: Settings, keep_repo: bool = False) -> Review:
    return ReviewRun(settings).run(keep_repo=keep_repo)


# ------------------------------------------------------------------ helpers


def _match_path(path: str, diff_paths: List[str]) -> str:
    """Map a model-written path onto an actual diff path (handles a/ b/ prefixes, leading ./, partial suffixes)."""
    if not path:
        return path
    p = path.strip().lstrip("./")
    for pre in ("a/", "b/"):
        if p.startswith(pre) and p[len(pre):] in diff_paths:
            return p[len(pre):]
    if p in diff_paths:
        return p
    suffix = [d for d in diff_paths if d.endswith("/" + p) or p.endswith("/" + d)]
    if len(suffix) == 1:
        return suffix[0]
    base = p.split("/")[-1]
    same_base = [d for d in diff_paths if d.split("/")[-1] == base]
    if len(same_base) == 1:
        return same_base[0]
    return p  # not in diff: will be posted in the body


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def dedupe(findings: List[Finding]) -> List[Finding]:
    """Merge findings from different lenses that point at the same place & problem."""
    kept: List[Finding] = []
    for f in sorted(findings, key=lambda x: (x.severity_rank(), -x.confidence)):
        dup = None
        for k in kept:
            same_place = k.path == f.path and k.line is not None and f.line is not None and abs(k.line - f.line) <= 2 and k.side == f.side
            if same_place and (_similar(k.title, f.title) > 0.6 or _similar(k.explanation[:300], f.explanation[:300]) > 0.6):
                dup = k
                break
            if k.path == f.path and k.line == f.line and _similar(k.title, f.title) > 0.8:
                dup = k
                break
        if dup:
            dup.verification.setdefault("also_found_by", []).append(f.lens)
            dup.confidence = max(dup.confidence, f.confidence)
            if not dup.suggested_code and f.suggested_code:
                dup.suggested_code = f.suggested_code
            continue
        kept.append(f)
    return kept


def aggregate_votes(votes: List[Dict[str, Any]], prior: float) -> Dict[str, Any]:
    if not votes:
        return {"verdict": "unverified", "votes": 0, "confidence": prior, "tally": "0/0"}
    c = sum(1 for v in votes if v.get("verdict") == "confirmed")
    r = sum(1 for v in votes if v.get("verdict") == "refuted")
    u = len(votes) - c - r
    confs = []
    for v in votes:
        try:
            confs.append(max(0.0, min(1.0, float(v.get("confidence", 0.5)))))
        except (TypeError, ValueError):
            confs.append(0.5)
    mean_conf = sum(confs) / len(confs)
    if r > c:
        verdict = "refuted"
    elif c > r:
        verdict = "confirmed"
    elif c == r and c > 0:
        verdict = "uncertain"
    else:
        verdict = "uncertain"
    # blend: verification evidence dominates the finder's self-estimate
    confidence = round(0.3 * prior + 0.7 * mean_conf, 3)
    if verdict == "refuted":
        confidence = min(confidence, 0.3)
    reasons = [str(v.get("reasoning") or "")[:600] for v in votes if v.get("reasoning")]
    return {
        "verdict": verdict,
        "votes": len(votes),
        "tally": f"{c} confirmed / {r} refuted / {u} uncertain",
        "confidence": confidence,
        "reasoning": "\n---\n".join(reasons),
    }


def apply_corrections(f: Finding, votes: List[Dict[str, Any]], diff: ParsedDiff) -> None:
    """Apply the majority's corrections (severity/anchor/explanation) when consistent."""
    sev = [v.get("corrected_severity") for v in votes if v.get("corrected_severity") in SEVERITIES]
    if sev and len(sev) * 2 > len(votes):
        f.severity = max(set(sev), key=sev.count)
    lines = [v for v in votes if v.get("corrected_line") is not None]
    if lines and len(lines) * 2 > len(votes):
        v = lines[0]
        path = v.get("corrected_path") or f.path
        side = (v.get("corrected_side") or f.side or "RIGHT").upper()
        line = int(v["corrected_line"])
        snapped = diff.snap(path, side, line, radius=3)
        if snapped:
            f.path = path
            f.side, f.line = snapped
            if f.start_line is not None and f.start_line >= f.line:
                f.start_line = None
    expl = [v.get("improved_explanation") for v in votes if v.get("improved_explanation")]
    if expl:
        f.verification["improved_explanation"] = expl[0]
        if len(expl) * 2 > len(votes):
            f.explanation = str(expl[0])


def enforce_verdict(verdict: str, active: List[Finding], draft: bool = False) -> str:
    verdict = verdict if verdict in ("APPROVE", "COMMENT", "REQUEST_CHANGES") else "COMMENT"
    sev = [f.severity for f in active]
    if "critical" in sev:
        return "REQUEST_CHANGES"
    if any(f.severity == "high" and f.blocking for f in active) and not draft:
        return "REQUEST_CHANGES"
    if verdict == "APPROVE" and any(s in ("high", "medium") for s in sev):
        return "COMMENT"
    return verdict


def fallback_summary(findings: List[Finding], understanding: Understanding, good: List[str]) -> Tuple[str, str]:
    active = [f for f in findings if f.status == "active"]
    lines = [understanding.change_summary or "(no summary)", ""]
    if active:
        lines.append("**Key issues**")
        for f in active:
            lines.append(f"- `{f.path}{':' + str(f.line) if f.line else ''}` — **{f.severity}** {f.title}")
    if good:
        lines.append("")
        lines.append("**What's good**")
        lines.extend(f"- {g}" for g in good[:3])
    verdict = enforce_verdict("COMMENT" if active else "APPROVE", active)
    return verdict, "\n".join(lines)
