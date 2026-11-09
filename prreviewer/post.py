"""Turn a stored Review into a GitHub pull-request review and post it.

Rules (see DESIGN.md "Posting"):
  * every inline comment must anchor to a (path, side, line) that is inside the
    diff; we validate against the stored diff and snap to the nearest line in
    the same hunk (±3), otherwise the comment is moved into the review body
    under "Other notes";
  * `start_line` must be in the same hunk and < line, else it is dropped;
  * APPROVE / REQUEST_CHANGES on your own PR is rejected by GitHub (422) —
    we fall back to COMMENT and say so;
  * after a successful post the local draft is deleted (unless --keep).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import log
from .diff import ParsedDiff, parse_unified_diff
from .github import GitHubClient, GitHubError
from .schema import Finding, Review, SEVERITY_RANK
from .store import ReviewStore, now_iso
from .urls import PRRef

FOOTER = "\n\n---\n<sub>Reviewed with [pr-reviewer](https://github.com/Ami-sh/pr-reviewer) — an independent one-shot LLM review. Comments were human-edited before posting.</sub>"


def load_diff(store: ReviewStore, rid: str) -> Optional[ParsedDiff]:
    text = store.read_text(rid, "diff.patch")
    if text is None:
        return None
    return parse_unified_diff(text)


def build_payload(review: Review, diff: Optional[ParsedDiff], event_override: Optional[str] = None, footer: bool = True) -> Tuple[Dict[str, Any], List[Finding], List[Finding]]:
    """Returns (payload, inline_findings, unanchored_findings)."""
    comments: List[Dict[str, Any]] = []
    inline: List[Finding] = []
    unanchored: List[Finding] = []
    for f in sorted(review.active_findings(), key=lambda x: (x.severity_rank(), x.path, x.line or 0)):
        if f.line is None or not f.path:
            unanchored.append(f)
            continue
        side, line = f.side or "RIGHT", int(f.line)
        if diff is not None:
            snapped = diff.snap(f.path, side, line)
            if snapped is None:
                unanchored.append(f)
                continue
            side, line = snapped
        c: Dict[str, Any] = {"path": f.path, "line": line, "side": side, "body": f.render_body()}
        if f.start_line and int(f.start_line) < line:
            start = int(f.start_line)
            ok = True
            if diff is not None:
                h1, h2 = diff.hunk_for(f.path, side, start), diff.hunk_for(f.path, side, line)
                ok = h1 is not None and h1 is h2
            if ok:
                c["start_line"] = start
                c["start_side"] = side
            elif f.suggested_code:
                # a suggestion block replaces start_line..line; without a valid range, drop the block
                c["body"] = f.render_body().split("```suggestion")[0].rstrip()
        comments.append(c)
        inline.append(f)

    body = review.summary.strip()
    if unanchored:
        body += "\n\n### Other notes (not anchored to a diff line)\n"
        for f in unanchored:
            loc = f"`{f.path}`" + (f":{f.line}" if f.line else "")
            body += f"\n- **{f.severity}** {loc} — {f.title}\n  {(f.body or f.explanation).strip()}\n"
    if footer:
        body += FOOTER
    event = event_override or review.verdict or "COMMENT"
    payload = {"body": body, "event": event, "comments": comments}
    return payload, inline, unanchored


def post_review(store: ReviewStore, rid: str, *, keep: bool = False, dry_run: bool = False, event_override: Optional[str] = None, footer: bool = True, token: Optional[str] = None) -> Dict[str, Any]:
    review = store.load_review(rid)
    pr = review.pr
    ref = PRRef(pr.get("host", "github.com"), pr["owner"], pr["repo"], int(pr["number"]))
    diff = load_diff(store, rid)
    payload, inline, unanchored = build_payload(review, diff, event_override=event_override, footer=footer)

    log.info(f"{len(inline)} inline comment(s), {len(unanchored)} in the body, event={payload['event']}")
    if dry_run:
        return {"dry_run": True, "payload": payload}

    client = GitHubClient(ref, token=token)
    if not client.token:
        raise GitHubError("no GitHub token; set GITHUB_TOKEN or run `gh auth login`")

    # GitHub forbids APPROVE/REQUEST_CHANGES on your own PR
    me = client.whoami()
    if me and me.lower() == str(pr.get("author", "")).lower() and payload["event"] in ("APPROVE", "REQUEST_CHANGES"):
        log.warn(f"you ({me}) authored this PR; GitHub does not allow {payload['event']} on your own PR — posting as COMMENT")
        payload["event"] = "COMMENT"

    try:
        result = client.post_review(payload)
    except GitHubError as e:
        msg = str(e)
        if "422" in msg and payload["comments"]:
            # Most likely a stale diff (PR was pushed to since the review) → retry without inline anchors
            log.warn("GitHub rejected an inline anchor (422). The PR head may have moved. Retrying with all comments in the body.")
            fallback = dict(payload)
            fallback["comments"] = []
            fallback["body"] = payload["body"].replace(FOOTER, "") + "\n\n### Inline comments (could not be anchored — the PR changed since the review)\n"
            for c in payload["comments"]:
                fallback["body"] += f"\n- `{c['path']}:{c['line']}` ({c['side']})\n  " + c["body"].replace("\n", "\n  ") + "\n"
            if footer:
                fallback["body"] += FOOTER
            result = client.post_review(fallback)
            payload = fallback
        else:
            raise

    posted = {
        "review_id": result.get("id"),
        "html_url": result.get("html_url"),
        "event": payload["event"],
        "inline": len(payload["comments"]),
        "posted_at": now_iso(),
    }
    review.posted = posted
    store.save_review(review)
    store.set_status(rid, "posted", posted=posted)
    log.info(f"posted: {posted['html_url']}")
    if not keep:
        store.delete(rid)
        log.info(f"deleted local draft {rid}")
    return posted
