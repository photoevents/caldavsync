"""Interactive decider for `--review` mode.

For each change the sync wants to make, the user chooses what to do. Choices
that say "ignore forever" are persisted in the database (the engine writes them
to the ignore-list), so automatic runs never propose that event again.

  [a] apply   - make this change now
  [s] skip    - skip it this run (Enter does this); it'll come up again
  [i] ignore  - never propose this event again (persisted)
  [q] quit    - stop reviewing; apply nothing further
"""

from __future__ import annotations


class ReviewQuit(BaseException):
    """Raised to abort review. Inherits BaseException so the engine's
    per-event `except Exception` guard doesn't swallow it."""


def make_review_decider():
    """Return a decider(action, uid, summary) -> 'apply' | 'skip' | 'ignore'."""

    def decide(action: str, uid: str, summary: str) -> str:
        while True:
            print(f"\n  {action}")
            choice = input("  [a]pply / [s]kip / [i]gnore forever / [q]uit: ").strip().lower()
            if choice in ("a", "apply"):
                return "apply"
            if choice in ("s", "skip", ""):
                return "skip"
            if choice in ("i", "ignore"):
                return "ignore"
            if choice in ("q", "quit"):
                raise ReviewQuit()
            print("  Please answer a, s, i, or q.")

    return decide
