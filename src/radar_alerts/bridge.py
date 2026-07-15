"""
bridge.py — the alerting bridge: turn two retained snapshots into one early-warning digest.

Uses the corrected ``adapter`` (COLUMN_MAP, bridge-join notices, verified source_url — never
templated). Scoped to what Shot A needs (AC-14: one digest over a snapshot pair); the full
DR-1..DR-6 / SMTP / state engine is a separate sub-loop. Deterministic: no wall-clock; all
orderings are total; labels come from the snapshot dir names (or explicit args).

A "snapshot dir" is a star-schema export produced by export.snapshot.archive_snapshot:
  fact_recompete_candidates.csv, bridge_award_opportunity_links.csv, fact_opportunity_notices.csv
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from radar_alerts import adapter
from utils.coerce import nan_str

# Rules emitted, in digest order.
RULE_ORDER = ("New candidate", "Tier change", "New notice link")


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame()


def _notice_links(snapshot_dir: Path) -> dict[str, list[str]]:
    """candidate_id -> sorted list of linked notice ids (the bridge JOIN, not a column)."""
    bridge = _read_csv(snapshot_dir / f"{adapter.NOTICE_LINK_TABLE}.csv")
    out: dict[str, list[str]] = {}
    if bridge.empty:
        return out
    key, nid = adapter.NOTICE_LINK_JOIN_KEY, adapter.NOTICE_LINK_ID_FIELD
    for _, row in bridge.iterrows():
        cand, notice = row.get(key), row.get(nid)
        if cand and notice and not pd.isna(cand) and not pd.isna(notice):
            out.setdefault(str(cand), [])
            if str(notice) not in out[str(cand)]:
                out[str(cand)].append(str(notice))
    return {k: sorted(v) for k, v in out.items()}


def notice_source_urls(snapshot_dir: str | Path) -> dict[str, str]:
    """notice_id -> its verified source_url (from fact_opportunity_notices; never templated)."""
    notices = _read_csv(Path(snapshot_dir) / f"{adapter.NOTICE_TABLE}.csv")
    if notices.empty or adapter.NOTICE_ID_FIELD not in notices.columns:
        return {}
    urls: dict[str, str] = {}
    for _, row in notices.iterrows():
        nid = row.get(adapter.NOTICE_ID_FIELD)
        url = row.get(adapter.NOTICE_SOURCE_URL_FIELD)
        if nid and isinstance(url, str) and url.strip():
            urls[str(nid)] = url.strip()
    return urls


def load_snapshot(snapshot_dir: str | Path) -> pd.DataFrame:
    """Load candidates, rename real columns to the engine contract (COLUMN_MAP), and derive
    ``notice_ids`` via the bridge join. ``source_url`` is carried through verbatim.

    Fails loud: a missing ``fact_recompete_candidates.csv`` raises ``FileNotFoundError`` —
    a typo'd snapshot path must NOT silently produce a confident "0 changes" digest. (The
    bridge/notice tables are allowed to be absent — legitimately empty when SAM has no data.)
    """
    d = Path(snapshot_dir)
    candidates_csv = d / "fact_recompete_candidates.csv"
    if not candidates_csv.exists():
        raise FileNotFoundError(f"snapshot has no fact_recompete_candidates.csv: {candidates_csv}")
    cand = _read_csv(candidates_csv)
    if cand.empty:
        return cand
    real_to_contract = {real: contract for contract, real in adapter.COLUMN_MAP.items() if real in cand.columns}
    df = cand.rename(columns=real_to_contract)
    links = _notice_links(d)
    df["notice_ids"] = df["award_key"].map(lambda k: links.get(str(k), []))
    return df


def _row_val(row: pd.Series, col: str) -> str:
    return nan_str(row.get(col))


def diff_snapshots(prev: pd.DataFrame, curr: pd.DataFrame) -> list[dict]:
    """Alert items between two loaded snapshots: new candidates, tier changes, new notice
    links. Each item decomposes to award_key + rule + old->new + a source link (honesty)."""
    items: list[dict] = []
    prev_by = (
        prev.set_index("award_key") if not prev.empty else pd.DataFrame().set_index(pd.Index([], name="award_key"))
    )
    curr_by = (
        curr.set_index("award_key") if not curr.empty else pd.DataFrame().set_index(pd.Index([], name="award_key"))
    )
    prev_keys = set(prev_by.index)

    for key, row in curr_by.iterrows():
        base = {
            "award_key": str(key),
            "recipient_name": _row_val(row, "recipient_name"),
            "agency": _row_val(row, "agency"),
            "tier": _row_val(row, "tier"),
            "source_url": _row_val(row, "source_url"),
        }
        if key not in prev_keys:
            items.append(
                {**base, "rule": "New candidate", "old": None, "new": base["tier"], "notices": row["notice_ids"]}
            )
            continue
        prow = prev_by.loc[key]
        prev_tier = _row_val(prow, "tier")
        if base["tier"] != prev_tier:
            items.append({**base, "rule": "Tier change", "old": prev_tier, "new": base["tier"], "notices": []})
        new_notices = sorted(set(row["notice_ids"]) - set(prow["notice_ids"]))
        if new_notices:
            items.append({**base, "rule": "New notice link", "old": None, "new": None, "notices": new_notices})

    items.sort(key=lambda it: (RULE_ORDER.index(it["rule"]), it["award_key"]))
    return items


def render_digest(
    items: list[dict], prev_label: str, curr_label: str, notice_urls: dict[str, str] | None = None
) -> str:
    """Render one plaintext digest. Every item shows its rule, old->new, and a public source
    link (or 'no direct link' — never fabricated)."""
    notice_urls = notice_urls or {}
    lines = [
        f"Radar digest: {prev_label} -> {curr_label}",
        f"{len(items)} change(s) across {len({it['award_key'] for it in items})} candidate(s).",
        "",
    ]
    for rule in RULE_ORDER:
        section = [it for it in items if it["rule"] == rule]
        if not section:
            continue
        lines.append(f"## {rule} ({len(section)})")
        for it in section:
            why = f"{it['old']} -> {it['new']}" if it["rule"] == "Tier change" else it["tier"]
            link = it["source_url"] if it["source_url"] else "no direct link"
            lines.append(f"- {it['award_key']} — {it['recipient_name']} — {it['agency']} — {why} — {link}")
            for nid in it["notices"]:
                nlink = notice_urls.get(nid, f"notice {nid} (no direct link)")
                lines.append(f"    · notice {nid}: {nlink}")
        lines.append("")
    lines.append("Every item above links to its public source record where one is available.")
    return "\n".join(lines)


def build_digest(
    prev_dir: str | Path, curr_dir: str | Path, prev_label: str | None = None, curr_label: str | None = None
) -> str:
    """Load two snapshots, diff them, and render one digest. Labels default to dir names."""
    prev_dir, curr_dir = Path(prev_dir), Path(curr_dir)
    prev = load_snapshot(prev_dir)
    curr = load_snapshot(curr_dir)
    items = diff_snapshots(prev, curr)
    urls = {**notice_source_urls(prev_dir), **notice_source_urls(curr_dir)}
    return render_digest(items, prev_label or prev_dir.name, curr_label or curr_dir.name, urls)
