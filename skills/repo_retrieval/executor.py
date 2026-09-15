"""Deterministic lexical repository retrieval with citation tracing.

The skill ranks fixed-size line windows ("chunks") of workspace text files
against a query with a small TF-IDF pass. It is offline, has no external
service dependency, and returns ``path:start-end`` citations so every match
can be traced back to repository evidence.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Iterator

from skills.path_filter import IGNORED_DIRECTORIES, should_scan

DEFAULT_CHUNK_LINES = 40
DEFAULT_OVERLAP_LINES = 10
DEFAULT_MAX_CHUNKS = 8
MIN_CHUNK_LINES = 5
MAX_CHUNK_LINES = 200
MAX_CHUNKS_LIMIT = 50
MAX_SCANNED_FILES = 300
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_TOTAL_CHUNKS = 4000
EXCERPT_LIMIT = 800
BINARY_SCAN_BYTES = 2048

_WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase word, identifier and CJK-bigram tokens."""

    tokens: list[str] = []
    for word in _WORD_PATTERN.findall(_CAMEL_BOUNDARY.sub(" ", text)):
        tokens.extend(piece for piece in word.lower().split("_") if piece)
    for run in _CJK_PATTERN.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _candidate_files(repo: Path) -> tuple[list[Path], bool]:
    """Return candidate files and whether the configured file cap was hit."""

    files: list[Path] = []
    for root, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name not in IGNORED_DIRECTORIES
        )
        for name in sorted(filenames):
            path = Path(root) / name
            if not should_scan(repo, path):
                continue
            if len(files) >= MAX_SCANNED_FILES:
                return files, True
            files.append(path)
    return files, False


def _read_text(path: Path) -> tuple[str, int] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:BINARY_SCAN_BYTES]:
        return None
    return raw.decode("utf-8", errors="ignore"), len(raw)


def _chunk_windows(
    lines: list[str], chunk_lines: int, overlap_lines: int
) -> Iterator[tuple[int, int, str]]:
    """Yield 1-based (start_line, end_line, body) windows over fixed strides."""

    stride = chunk_lines - overlap_lines
    index = 0
    total = len(lines)
    while index < total:
        window = lines[index : index + chunk_lines]
        start = index + 1
        end = index + len(window)
        if any(line.strip() for line in window):
            yield start, end, "\n".join(window)
        if end >= total:
            return
        index += stride


def _collect_chunks(
    repo: Path, chunk_lines: int, overlap_lines: int
) -> tuple[list[tuple[str, int, int, str]], dict[str, object]]:
    """Return chunks plus explicit corpus coverage and truncation metadata."""

    chunks: list[tuple[str, int, int, str]] = []
    scanned_files = 0
    skipped_files = 0
    total_bytes = 0
    limit_reasons: list[str] = []
    candidates, file_limit_reached = _candidate_files(repo)
    if file_limit_reached:
        limit_reasons.append("max_scanned_files")
    for path in candidates:
        loaded = _read_text(path)
        if loaded is None:
            skipped_files += 1
            continue
        text, byte_count = loaded
        if total_bytes + byte_count > MAX_TOTAL_BYTES:
            skipped_files += 1
            if "max_total_bytes" not in limit_reasons:
                limit_reasons.append("max_total_bytes")
            continue
        scanned_files += 1
        total_bytes += byte_count
        relative = path.relative_to(repo).as_posix()
        for start, end, body in _chunk_windows(
            text.splitlines(), chunk_lines, overlap_lines
        ):
            chunks.append((relative, start, end, body))
            if len(chunks) >= MAX_TOTAL_CHUNKS:
                limit_reasons.append("max_total_chunks")
                return chunks, {
                    "candidate_files": len(candidates),
                    "scanned_files": scanned_files,
                    "skipped_files": skipped_files,
                    "scanned_bytes": total_bytes,
                    "truncated": True,
                    "limit_reasons": limit_reasons,
                }
    return chunks, {
        "candidate_files": len(candidates),
        "scanned_files": scanned_files,
        "skipped_files": skipped_files,
        "scanned_bytes": total_bytes,
        "truncated": bool(limit_reasons),
        "limit_reasons": limit_reasons,
    }


def _path_tokens(relative: str) -> set[str]:
    """Tokenize path components without letting common extensions dominate."""

    path_without_suffix = Path(relative).with_suffix("").as_posix()
    return set(_tokenize(path_without_suffix.replace("/", " ")))


def _rank_chunks(
    query_terms: set[str], chunks: list[tuple[str, int, int, str]]
) -> list[tuple[float, str, int, int, str]]:
    """Score chunks with TF-IDF and rank by score, path, then start line."""

    term_frequencies: list[Counter[str]] = []
    path_term_sets: list[set[str]] = []
    document_counts: Counter[str] = Counter()
    for relative, _, _, body in chunks:
        counts = Counter(_tokenize(body))
        path_terms = _path_tokens(relative)
        term_frequencies.append(counts)
        path_term_sets.append(path_terms)
        for term in query_terms:
            if counts[term] or term in path_terms:
                document_counts[term] += 1
    total = len(chunks)
    idf = {
        term: math.log((1 + total) / (1 + document_counts[term])) + 1
        for term in query_terms
    }
    scored: list[tuple[float, str, int, int, str]] = []
    for index, counts in enumerate(term_frequencies):
        body_score = sum(counts[term] * idf[term] for term in query_terms)
        path_score = sum(
            2.0 * idf[term]
            for term in query_terms
            if term in path_term_sets[index]
        )
        score = body_score + path_score
        if score > 0:
            relative, start, end, body = chunks[index]
            scored.append((score, relative, start, end, body))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return scored


def _excerpt(
    body: str, start_line: int, query_terms: set[str]
) -> dict[str, object]:
    """Return a line-numbered excerpt centered on the first actual match."""

    lines = body.splitlines()
    matched_index = next(
        (
            index
            for index, line in enumerate(lines)
            if query_terms.intersection(_tokenize(line))
        ),
        0,
    )
    left = max(0, matched_index - 3)
    right = min(len(lines), matched_index + 4)

    def render(first: int, last: int) -> str:
        return "\n".join(
            f"{start_line + index}: {lines[index]}" for index in range(first, last)
        )

    excerpt = render(left, right)
    while len(excerpt) > EXCERPT_LIMIT and right - left > 1:
        if matched_index - left > right - matched_index - 1:
            left += 1
        else:
            right -= 1
        excerpt = render(left, right)

    shortened_line = False
    if len(excerpt) > EXCERPT_LIMIT:
        line = lines[matched_index]
        lowered = line.lower()
        positions = [
            lowered.find(term.lower())
            for term in query_terms
            if lowered.find(term.lower()) >= 0
        ]
        match_position = min(positions) if positions else 0
        prefix = f"{start_line + matched_index}: "
        available = max(1, EXCERPT_LIMIT - len(prefix) - 2)
        segment_start = max(0, match_position - available // 2)
        segment_start = min(segment_start, max(0, len(line) - available))
        segment = line[segment_start : segment_start + available]
        excerpt = prefix
        if segment_start:
            excerpt += "…"
        excerpt += segment
        if segment_start + available < len(line):
            excerpt += "…"
        shortened_line = True
        left = right = matched_index

    excerpt_end = matched_index if shortened_line else max(left, right - 1)
    return {
        "excerpt": excerpt,
        "excerpt_start_line": start_line + left,
        "excerpt_end_line": start_line + excerpt_end,
        "excerpt_truncated": shortened_line or left > 0 or right < len(lines),
    }


def run(inputs: dict) -> dict:
    repo = Path(str(inputs.get("repo_path", "")))
    if not repo.is_dir():
        return {"status": "error", "error": f"repo not found: {repo}"}
    query = str(inputs.get("query", "")).strip()
    if not query:
        return {"status": "error", "error": "query must not be empty"}
    try:
        chunk_lines = int(inputs.get("chunk_lines", DEFAULT_CHUNK_LINES))
        overlap_lines = int(inputs.get("overlap_lines", DEFAULT_OVERLAP_LINES))
        max_chunks = int(inputs.get("max_chunks", DEFAULT_MAX_CHUNKS))
    except (TypeError, ValueError):
        return {"status": "error", "error": "chunk parameters must be integers"}
    if not MIN_CHUNK_LINES <= chunk_lines <= MAX_CHUNK_LINES:
        return {
            "status": "error",
            "error": f"chunk_lines must be between {MIN_CHUNK_LINES} and {MAX_CHUNK_LINES}",
        }
    if not 0 <= overlap_lines < chunk_lines:
        return {"status": "error", "error": "overlap_lines must be smaller than chunk_lines"}
    if not 1 <= max_chunks <= MAX_CHUNKS_LIMIT:
        return {"status": "error", "error": f"max_chunks must be between 1 and {MAX_CHUNKS_LIMIT}"}
    query_terms = set(_tokenize(query))
    if not query_terms:
        return {"status": "error", "error": "query has no searchable terms"}

    chunks, coverage = _collect_chunks(repo, chunk_lines, overlap_lines)
    ranked = _rank_chunks(query_terms, chunks)
    matches = []
    for score, relative, start, end, body in ranked[:max_chunks]:
        matched_terms = sorted(
            query_terms.intersection(
                set(_tokenize(body)).union(_path_tokens(relative))
            )
        )
        matches.append({
            "path": relative,
            "start_line": start,
            "end_line": end,
            "score": round(score, 6),
            "citation": f"{relative}:{start}-{end}",
            "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "repository_revision": inputs.get("repository_revision"),
            "matched_terms": matched_terms,
            **_excerpt(body, start, query_terms),
        })
    return {
        "status": "ok",
        "data": {
            "query": query,
            "repository_revision": inputs.get("repository_revision"),
            "scanned_files": coverage["scanned_files"],
            "total_chunks": len(chunks),
            "chunk_lines": chunk_lines,
            "overlap_lines": overlap_lines,
            "result_truncated": len(ranked) > max_chunks,
            "coverage": coverage,
            "matches": matches,
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
