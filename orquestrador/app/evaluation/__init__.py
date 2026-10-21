"""AI extraction evaluation runner and set.

Scores the real purchase extraction pipeline (``app.purchase`` through
``LlmPort``) against a versioned, hand-written set of expected outputs, and
gates a provider swap behind the per-item accuracy bar.
"""
