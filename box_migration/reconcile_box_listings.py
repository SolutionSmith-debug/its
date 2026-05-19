#!/usr/bin/env python3
"""Reconcile parse_job_v3 against live Box folder listings.

Reads the per-portfolio folder listings produced by the Box export
(`folders__N. <portfolio>.txt`, one path per line, leading `./`),
runs each folder name through the v3 parser's claim chain, and emits a
markdown coverage report:

  * per-portfolio: schema classification, top-level claim coverage,
    chaos-flag tallies, and the top unclaimed names.
  * global: schema distribution, chaos-pattern totals, and a deduped
    list of unclaimed names across all 10 portfolios.

The "claim chain" walks each folder name through several v3 entry
points in priority order, recording which one (if any) recognized the
name:

  1. parse_active_subjob — recognizes the 5 sub-job ID formats
     (full_dot, three_digit, dashed, letter_lc, letter_uc).
  2. parse_portfolio_subject — recognizes "N. Portfolio <Subject>".
  3. parse_development_subject — recognizes the pre-EPC 8-subject tree.
  4. parse_folder kind in {SUBJECT, UTILITY, SHARED} — canonical
     non-job folders.
  5. parse_folder kind == JOB with job_id_kind != NAMED_ONLY — an
     identifiable job/portfolio root.

A name that survives all five claims is "unclaimed" — either a genuine
parser gap or a free-text folder name that doesn't need a structured
parse (e.g., "From Evergreen EPC", "Submittals", "Permit").

Chaos flags are tallied separately via detect_chaos and the
detect_duplicate_numbers_at_level group check.

Usage (must run from inside box_migration/ so top-level imports of
parse_job, parse_job_v2 resolve):

    cd box_migration
    python reconcile_box_listings.py
    python reconcile_box_listings.py --listings-dir /path/to/listings
    python reconcile_box_listings.py --output report.md

Listings stay in ~/Downloads/ per operator decision (don't commit
customer portfolio names into git). This script lives in the repo;
its output (the report) is intended to be committed.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import parse_job_v3 as p


DEFAULT_LISTINGS_DIR = Path.home() / "Downloads" / "Box_listings_for_Seth"
PORTFOLIO_FILE_RE = re.compile(r"^folders__(\d+)\.\s*(.+?)\.txt$")


# ---- Listing IO ----------------------------------------------------------


@dataclass
class PortfolioListing:
    """One portfolio's folder listing, split into normalized paths."""

    portfolio_number: int
    portfolio_name: str
    source_path: Path
    relative_paths: list[str] = field(default_factory=list)

    @property
    def top_level_names(self) -> list[str]:
        """Unique top-level folder names (path components with no '/')."""
        seen: list[str] = []
        for rel in self.relative_paths:
            head = rel.split("/", 1)[0]
            if head not in seen:
                seen.append(head)
        return seen

    @property
    def all_folder_names(self) -> list[str]:
        """Every path component across every path. NOT deduped — we want raw counts."""
        names: list[str] = []
        for rel in self.relative_paths:
            names.extend(rel.split("/"))
        return names


def _strip_bom_and_prefix(line: str) -> str:
    line = line.lstrip("﻿").rstrip("\r\n")
    if line.startswith("./"):
        line = line[2:]
    return line


def load_listing(path: Path) -> PortfolioListing:
    m = PORTFOLIO_FILE_RE.match(path.name)
    if not m:
        raise ValueError(f"Unexpected listing filename: {path.name!r}")
    number = int(m.group(1))
    portfolio_name = m.group(2).strip()

    rels: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            cleaned = _strip_bom_and_prefix(raw)
            if cleaned:
                rels.append(cleaned)

    return PortfolioListing(
        portfolio_number=number,
        portfolio_name=portfolio_name,
        source_path=path,
        relative_paths=rels,
    )


def discover_listings(listings_dir: Path) -> list[PortfolioListing]:
    # Walrus binds the match so mypy narrows from `Match[str] | None` to
    # `Match[str]` for the truthy branch, AND avoids the double regex call
    # the previous double-evaluation form had.
    files = sorted(
        listings_dir.glob("folders__*.txt"),
        key=lambda p: int(m.group(1)) if (m := PORTFOLIO_FILE_RE.match(p.name)) else 9999,
    )
    if not files:
        raise FileNotFoundError(
            f"No folders__*.txt files in {listings_dir}; check --listings-dir."
        )
    return [load_listing(f) for f in files]


# ---- Claim resolution ----------------------------------------------------


CLAIM_LABELS: tuple[str, ...] = (
    "active_subjob",           # parse_active_subjob.kind != 'not_subjob'
    "portfolio_subject",       # parse_portfolio_subject matched
    "development_subject",     # parse_development_subject matched
    "subsubject",              # parse_subsubject matched (N.M, N.M.K, Na.)
    "vendor_sub",              # parse_vendor_sub matched (V12., S10., etc.)
    "date_prefix",             # parse_date_prefix matched (R./S./ISO)
    "canonical_non_job",       # parse_folder kind == SUBJECT | UTILITY | SHARED
    "identifiable_job",        # parse_folder kind == JOB w/ recognized job_id
    "unclaimed",               # nothing matched
)


@dataclass
class Claim:
    name: str
    label: str
    detail: str = ""


def resolve_claim(name: str) -> Claim:
    """Walk the priority chain and return the first claim that recognizes `name`."""
    subjob = p.parse_active_subjob(name)
    if subjob.kind != "not_subjob":
        return Claim(name=name, label="active_subjob", detail=subjob.kind)

    portfolio = p.parse_portfolio_subject(name)
    if portfolio is not None:
        return Claim(name=name, label="portfolio_subject")

    dev = p.parse_development_subject(name)
    if dev is not None:
        return Claim(name=name, label="development_subject")

    subsub = p.parse_subsubject(name)
    if subsub is not None:
        return Claim(name=name, label="subsubject", detail=subsub.kind)

    vendor_sub = p.parse_vendor_sub(name)
    if vendor_sub is not None:
        return Claim(name=name, label="vendor_sub", detail=vendor_sub.kind)

    date_prefix = p.parse_date_prefix(name)
    if date_prefix is not None:
        return Claim(name=name, label="date_prefix", detail=date_prefix.direction)

    parsed = p.parse_folder(name)
    kind = parsed.folder_kind.value
    if kind in {"subject", "utility", "shared"}:
        return Claim(name=name, label="canonical_non_job", detail=kind)

    if parsed.folder_kind.value == "job" and parsed.job_id_kind is not None:
        kid = parsed.job_id_kind.value if hasattr(parsed.job_id_kind, "value") else str(parsed.job_id_kind)
        if kid != "named_only":
            return Claim(name=name, label="identifiable_job", detail=kid)

    return Claim(name=name, label="unclaimed")


# ---- Per-portfolio analysis ----------------------------------------------


@dataclass
class PortfolioReport:
    listing: PortfolioListing
    schema: str
    schema_signatures: list[str]
    top_level_claims: list[Claim]
    claim_counts: Counter[str]
    chaos_counts: Counter[str]
    chaos_examples: dict[str, list[str]]
    unclaimed_names: Counter[str]
    duplicate_number_groups: list


def analyze_portfolio(listing: PortfolioListing) -> PortfolioReport:
    top_level = listing.top_level_names

    # 1. Schema classification on the top-level set.
    schema, sigs = p.classify_schema(top_level)

    # 2. Top-level claim chain.
    top_claims = [resolve_claim(n) for n in top_level]

    # 3. Claim counts at all levels (deduped per path component name to avoid
    #    weighting toward repeated subfolder names).
    seen_names: set[str] = set()
    claim_counts: Counter[str] = Counter()
    unclaimed_names: Counter[str] = Counter()
    chaos_counts: Counter[str] = Counter()
    chaos_examples: dict[str, list[str]] = defaultdict(list)

    for name in listing.all_folder_names:
        if name in seen_names:
            continue
        seen_names.add(name)

        claim = resolve_claim(name)
        claim_counts[claim.label] += 1
        if claim.label == "unclaimed":
            unclaimed_names[name] += 1

        for flag in p.detect_chaos(name):
            chaos_counts[flag.pattern] += 1
            if len(chaos_examples[flag.pattern]) < 5:
                chaos_examples[flag.pattern].append(name)

    # 4. Duplicate-number-at-level: applies per directory, not per name.
    dup_groups = []
    by_parent: dict[str, list[str]] = defaultdict(list)
    for rel in listing.relative_paths:
        parent, _, child = rel.rpartition("/")
        by_parent[parent].append(child)
    for parent_path, children in by_parent.items():
        unique_children = list(dict.fromkeys(children))
        flags = p.detect_duplicate_numbers_at_level(unique_children)
        if flags:
            dup_groups.append((parent_path or "<root>", flags))

    return PortfolioReport(
        listing=listing,
        schema=schema.value if hasattr(schema, "value") else str(schema),
        schema_signatures=sigs,
        top_level_claims=top_claims,
        claim_counts=claim_counts,
        chaos_counts=chaos_counts,
        chaos_examples=chaos_examples,
        unclaimed_names=unclaimed_names,
        duplicate_number_groups=dup_groups,
    )


# ---- Reporting -----------------------------------------------------------


def _bar(count: int, total: int, width: int = 24) -> str:
    if total == 0:
        return " " * width
    filled = round(count / total * width)
    return "█" * filled + "░" * (width - filled)


def format_report(reports: list[PortfolioReport]) -> str:
    out: list[str] = []
    out.append("# parse_job_v3 reconcile report — Box listings\n")
    out.append(
        f"Source: {reports[0].listing.source_path.parent}  \n"
        f"Portfolios: {len(reports)}  \n"
    )

    # Global totals first
    global_claims: Counter[str] = Counter()
    global_chaos: Counter[str] = Counter()
    global_unclaimed: Counter[str] = Counter()
    schema_dist: Counter[str] = Counter()
    for r in reports:
        global_claims.update(r.claim_counts)
        global_chaos.update(r.chaos_counts)
        global_unclaimed.update(r.unclaimed_names)
        schema_dist[r.schema] += 1

    out.append("## Global summary\n")

    total_unique = sum(global_claims.values())
    out.append(f"Total unique folder-name strings across all portfolios: **{total_unique}**\n")

    out.append("### Claim coverage (unique names)\n")
    out.append("| Claim | Count | Share |  |")
    out.append("|---|---:|---:|---|")
    for label in CLAIM_LABELS:
        n = global_claims.get(label, 0)
        share = (n / total_unique * 100) if total_unique else 0.0
        out.append(f"| {label} | {n} | {share:5.1f}% | {_bar(n, total_unique)} |")
    out.append("")

    out.append("### Schema classification distribution\n")
    out.append("| Schema | Portfolios |")
    out.append("|---|---:|")
    for sch, n in schema_dist.most_common():
        out.append(f"| {sch} | {n} |")
    out.append("")

    out.append("### Chaos-flag totals (unique names triggering each pattern)\n")
    if global_chaos:
        out.append("| Pattern | Count |")
        out.append("|---|---:|")
        for pat, n in global_chaos.most_common():
            out.append(f"| {pat} | {n} |")
    else:
        out.append("_No chaos patterns triggered across any portfolio._")
    out.append("")

    out.append("### Top unclaimed names (across all portfolios)\n")
    if global_unclaimed:
        out.append("Names below survive the entire claim chain (active_subjob → portfolio_subject → development_subject → canonical_non_job → identifiable_job) without being recognized. Many are legitimate free-text folder names; the list is the parser-gap candidate pool.\n")
        out.append("| Count | Name |")
        out.append("|---:|---|")
        for name, n in global_unclaimed.most_common(40):
            out.append(f"| {n} | `{name}` |")
    else:
        out.append("_No unclaimed names — every folder string was recognized._")
    out.append("")

    # Per-portfolio sections
    out.append("---\n")
    out.append("## Per-portfolio detail\n")

    for r in reports:
        out.append(f"### {r.listing.portfolio_number}. {r.listing.portfolio_name}\n")
        out.append(
            f"- Source: `{r.listing.source_path.name}`  \n"
            f"- Total folder paths: {len(r.listing.relative_paths)}  \n"
            f"- Unique folder-name strings: {sum(r.claim_counts.values())}  \n"
            f"- Top-level folder count: {len(r.listing.top_level_names)}  \n"
            f"- **Schema:** `{r.schema}`  "
            + (f"(signatures: {', '.join(r.schema_signatures)})" if r.schema_signatures else "")
            + "\n"
        )

        # Top-level claim table
        out.append("\n**Top-level folder claims**\n")
        out.append("| Folder | Claim | Detail |")
        out.append("|---|---|---|")
        for c in r.top_level_claims:
            out.append(f"| `{c.name}` | {c.label} | {c.detail or '—'} |")
        out.append("")

        # Claim counts
        out.append("**Claim counts (unique names in this portfolio)**\n")
        out.append("| Claim | Count |")
        out.append("|---|---:|")
        for label in CLAIM_LABELS:
            out.append(f"| {label} | {r.claim_counts.get(label, 0)} |")
        out.append("")

        # Chaos
        if r.chaos_counts:
            out.append("**Chaos flags**\n")
            out.append("| Pattern | Count | Examples |")
            out.append("|---|---:|---|")
            for pat, n in r.chaos_counts.most_common():
                ex = ", ".join(f"`{e}`" for e in r.chaos_examples[pat][:3])
                out.append(f"| {pat} | {n} | {ex} |")
            out.append("")
        else:
            out.append("_No chaos flags in this portfolio._\n")

        # Duplicate-number groups
        if r.duplicate_number_groups:
            out.append("**Duplicate-number-at-level findings**\n")
            for parent, flags in r.duplicate_number_groups:
                for f in flags:
                    out.append(f"- `{parent}/`: {f}")
            out.append("")

        # Unclaimed (top 20 in-portfolio)
        if r.unclaimed_names:
            out.append("**Unclaimed names (top 20)**\n")
            out.append("| Name |")
            out.append("|---|")
            for name, _ in r.unclaimed_names.most_common(20):
                out.append(f"| `{name}` |")
            out.append("")
        else:
            out.append("_No unclaimed names in this portfolio._\n")

        out.append("")

    return "\n".join(out)


# ---- CLI -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--listings-dir",
        type=Path,
        default=DEFAULT_LISTINGS_DIR,
        help=f"Directory containing folders__*.txt files (default: {DEFAULT_LISTINGS_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write markdown report to this path (default: stdout)",
    )
    args = parser.parse_args(argv)

    listings = discover_listings(args.listings_dir)
    reports = [analyze_portfolio(L) for L in listings]
    rendered = format_report(reports)

    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Report written to {args.output} ({len(rendered.splitlines())} lines).", file=sys.stderr)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
