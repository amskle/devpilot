"""Deprecated compatibility import for the LangGraph-backed pipeline."""

from runtime.compat_pipeline import main, run_pipeline

__all__ = ["main", "run_pipeline"]


if __name__ == "__main__":
    main()
