import tempfile
from pathlib import Path

import skills.repo_retrieval.executor as retrieval
from skills.repo_retrieval.executor import run


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_relevant_chunk_ranks_first_with_traceable_citation():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(
            repo,
            "src/auth.py",
            "def authenticate_user(token):\n    return validate_token(token)\n",
        )
        _write(repo, "src/other.py", "def unrelated_helper():\n    return 0\n")

        result = run({"repo_path": tmp, "query": "authenticate user token"})

        assert result["status"] == "ok"
        data = result["data"]
        assert data["scanned_files"] == 2
        assert data["total_chunks"] == 2
        top = data["matches"][0]
        assert top["path"] == "src/auth.py"
        assert top["citation"] == "src/auth.py:1-2"
        assert top["score"] > 0
        assert "authenticate_user" in top["excerpt"]
        assert top["excerpt_start_line"] == 1
        assert len(top["content_sha256"]) == 64
        assert top["matched_terms"] == ["authenticate", "token", "user"]


def test_unrelated_query_returns_no_matches_without_guessing():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo, "app.py", "value = 1\n")

        result = run({"repo_path": tmp, "query": "zzz-nonexistent-token"})

        assert result["status"] == "ok"
        assert result["data"]["matches"] == []
        assert result["data"]["scanned_files"] == 1


def test_chunk_windows_report_line_ranges_with_overlap():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        content = "\n".join(f"line-{index}" for index in range(100))
        _write(repo, "long.py", content + "\nmarker_token_here = True\n")

        result = run(
            {
                "repo_path": tmp,
                "query": "marker_token_here",
                "chunk_lines": 40,
                "overlap_lines": 10,
            }
        )

        matches = result["data"]["matches"]
        assert len(matches) == 1
        assert matches[0]["citation"] == "long.py:91-101"
        assert result["data"]["chunk_lines"] == 40
        assert result["data"]["overlap_lines"] == 10


def test_hidden_ignored_and_binary_paths_are_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo, ".hidden/secret.py", "shadow_token = 1\n")
        _write(repo, "node_modules/dep/index.js", "shadow_token = 2\n")
        _write(repo, "src/visible.py", "shadow_token = 3\n")
        (repo / "binary.py").write_bytes(b"\x00\x01shadow_token")

        result = run({"repo_path": tmp, "query": "shadow_token"})

        matches = result["data"]["matches"]
        assert [match["path"] for match in matches] == ["src/visible.py"]
        assert result["data"]["scanned_files"] == 1


def test_cjk_query_matches_chinese_content():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo, "docs/notes.md", "缓存失效导致数据不一致，需要重建索引。\n")
        _write(repo, "docs/other.md", "无关内容。\n")

        result = run({"repo_path": tmp, "query": "缓存失效"})

        assert result["status"] == "ok"
        assert result["data"]["matches"][0]["path"] == "docs/notes.md"


def test_identifier_tokenization_splits_camel_and_snake_case():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo, "a.py", "def loadConfig():\n    pass\n")
        _write(repo, "b.py", "def load_config():\n    pass\n")

        snake = run({"repo_path": tmp, "query": "load_config"})
        camel = run({"repo_path": tmp, "query": "loadConfig"})

        assert {match["path"] for match in snake["data"]["matches"]} == {"a.py", "b.py"}
        assert {match["path"] for match in camel["data"]["matches"]} == {"a.py", "b.py"}


def test_max_chunks_limits_and_scores_ties_break_deterministically():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        for name in ("c.py", "a.py", "b.py"):
            _write(repo, name, "shared_token = True\n")

        limited = run({"repo_path": tmp, "query": "shared_token", "max_chunks": 2})
        repeated = run({"repo_path": tmp, "query": "shared_token", "max_chunks": 2})

        assert [match["path"] for match in limited["data"]["matches"]] == ["a.py", "b.py"]
        assert limited == repeated


def test_default_selection_returns_at_most_one_chunk_per_file():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        repeated = "\n".join(
            f"needle value_{index}" for index in range(120)
        )
        _write(repo, "large.txt", repeated)
        _write(repo, "other.txt", "needle from another file\n")

        result = run(
            {
                "repo_path": tmp,
                "query": "needle",
                "chunk_lines": 20,
                "overlap_lines": 0,
            }
        )

        paths = [match["path"] for match in result["data"]["matches"]]
        assert paths.count("large.txt") == 1
        assert paths.count("other.txt") == 1
        assert result["data"]["max_chunks_per_file"] == 1
        assert result["data"]["result_truncated"] is True


def test_invalid_parameters_and_inputs_return_errors():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo, "app.py", "value = 1\n")

        assert run({"repo_path": "Z:/no/such/path", "query": "value"})["status"] == "error"
        assert run({"repo_path": tmp, "query": "  "})["status"] == "error"
        assert run({"repo_path": tmp, "query": "!!!"})["status"] == "error"
        assert (
            run({"repo_path": tmp, "query": "value", "overlap_lines": 40})["status"]
            == "error"
        )
        assert (
            run({"repo_path": tmp, "query": "value", "max_chunks": 0})["status"]
            == "error"
        )
        assert (
            run(
                {
                    "repo_path": tmp,
                    "query": "value",
                    "max_chunks_per_file": 0,
                }
            )["status"]
            == "error"
        )


def test_excerpt_is_centered_on_match_in_a_long_line():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo, "generated.txt", f"{'x' * 1500} needle_token {'y' * 1500}\n")

        result = run({"repo_path": tmp, "query": "needle_token"})

        match = result["data"]["matches"][0]
        assert "needle_token" in match["excerpt"]
        assert len(match["excerpt"]) <= retrieval.EXCERPT_LIMIT
        assert match["excerpt_start_line"] == match["excerpt_end_line"] == 1
        assert match["excerpt_truncated"] is True


def test_path_tokens_receive_a_deterministic_ranking_boost():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo, "src/payment_processor.py", "def run():\n    pass\n")
        _write(repo, "src/notes.py", "payment processor\n")

        result = run({"repo_path": tmp, "query": "payment processor"})

        assert result["data"]["matches"][0]["path"] == "src/payment_processor.py"


def test_scan_limits_are_explicit_in_coverage(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo, "a.py", "needle = 1\n")
        _write(repo, "b.py", "needle = 2\n")
        monkeypatch.setattr(retrieval, "MAX_SCANNED_FILES", 1)

        result = run({"repo_path": tmp, "query": "needle"})

        coverage = result["data"]["coverage"]
        assert coverage["truncated"] is True
        assert coverage["limit_reasons"] == ["max_scanned_files"]
        assert coverage["scanned_files"] == 1


def test_repository_revision_is_returned_with_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo, "app.py", "needle = 1\n")

        result = run(
            {
                "repo_path": tmp,
                "repository_revision": "abc123",
                "query": "needle",
            }
        )

        assert result["data"]["repository_revision"] == "abc123"
