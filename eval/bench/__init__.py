"""Prompt batteries + bench runner/report (``python -m bench.run_bench`` / ``python -m bench.report``).

Methodology: paired runs (same battery, same fixed judge model) across
generators; prompts stratified by tier (easy/medium/hard) and category.
"""
