"""Why a turn ended with no answer.

Every skill call a turn made, and what came back, is persisted on the assistant
message: `skills` holds [{name, arguments, result}, ...] and `reasoning` holds
the thinking stream. That is enough to tell a model looping on one call from a
model whose answer never reached the content channel -- which look identical
from the outside and have completely different fixes.

    python -m tools.why_silent            # the most recent empty turn
    python -m tools.why_silent <msg_id>
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime

from app.config import load_settings
from app.db import Database


def main() -> int:
    settings = load_settings()
    db = Database(settings.db_path)

    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    if wanted:
        row = db.query_one("SELECT * FROM messages WHERE id = ?", (wanted,))
    else:
        # The most recent assistant turn that produced nothing visible.
        row = db.query_one(
            """
            SELECT * FROM messages
             WHERE role = 'assistant' AND TRIM(COALESCE(content, '')) = ''
             ORDER BY created_at DESC LIMIT 1
            """
        )
    if row is None:
        print("No empty assistant turn found. Nothing to explain.")
        return 0

    calls = json.loads(row["skills"]) if row["skills"] else []
    when = datetime.fromtimestamp(row["created_at"] / 1000).strftime("%d-%m-%Y %H:%M")
    print(f"message {row['id']}  session {row['session_id']}  {when}")
    print(f"model {row['model']} via {row['provider']}")
    print(f"answer: {row['content']!r}")
    print(f"reasoning: {len(row['reasoning'] or '')} chars")
    print(f"skill calls: {len(calls)}\n")

    if not calls:
        print("No skill calls were recorded, so the model asked for nothing and")
        print("still wrote nothing. If it has reasoning, its answer went to the")
        print("thinking channel instead of the content channel.")
        if row["reasoning"]:
            print(f"\n--- reasoning (last 600 chars) ---\n{row['reasoning'][-600:]}")
        return 0

    signatures = Counter(
        f"{c['name']}({json.dumps(c.get('arguments', {}), sort_keys=True)})" for c in calls
    )
    for index, call in enumerate(calls, start=1):
        print(f"{index}. {call['name']}({json.dumps(call.get('arguments', {}))})")
        result = call.get("result")
        print(f"   -> {result if result is not None else '(never ran)'}"[:300])

    print("\n--- verdict ---")
    repeated = [(sig, n) for sig, n in signatures.items() if n > 1]
    if repeated:
        worst, count = max(repeated, key=lambda pair: pair[1])
        print(f"The identical call was made {count} times: {worst[:160]}")
        print("The model is not reading, or not accepting, the result it gets")
        print("back. Look at that result above: if it does not plainly say the")
        print("job is done, that is the thing to fix.")
    else:
        print("Every call was different, so this is not one call repeating.")
        print("The model kept finding more to do and never wrote an answer.")

    if row["reasoning"]:
        print(f"\n--- reasoning (last 600 chars) ---\n{row['reasoning'][-600:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
