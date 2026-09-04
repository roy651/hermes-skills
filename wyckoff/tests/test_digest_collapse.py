"""The concentration delta gate: full table on first sight or on a move, one line when nothing
moved. Run: .venv/bin/python tests/test_digest_collapse.py (or pytest)."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import digest

TABLE = ("⚖️ <b>Concentration</b>\n<pre>\nDGRO       47.8%  ████\nYL-KASPIT  23.3%  ███\n"
         "other 9    15.5%\n</pre>\n<b>15</b> positions, but an effective <b>3.4</b> — top two are "
         "<b>71%</b> of invested capital.")
TUESDAY = datetime(2026, 9, 8, 23, 30, tzinfo=digest.TZ)
MONDAY = datetime(2026, 9, 7, 23, 30, tzinfo=digest.TZ)


def test_collapse(tmp_path=None):
    digest.BRIEF_STATE = Path(tmp_path or "/tmp") / "brief_state_test.json"
    digest.BRIEF_STATE.unlink(missing_ok=True)

    assert digest._collapsed_or_full(TABLE, True, TUESDAY) == TABLE          # first sight: full
    collapsed = digest._collapsed_or_full(TABLE, True, TUESDAY)              # unchanged: one line
    assert collapsed.startswith("⚖️ <b>Concentration</b> — unchanged: DGRO 48% · YL-KASPIT 23%")
    assert "effective 3.4 — top two are 71%" in collapsed
    assert digest._collapsed_or_full(TABLE, True, MONDAY) == TABLE           # Mondays: full anyway
    moved = TABLE.replace("47.8%", "46.5%")
    assert digest._collapsed_or_full(moved, True, TUESDAY) == moved          # a full-point move: full
    digest.BRIEF_STATE.unlink(missing_ok=True)


if __name__ == "__main__":
    test_collapse()
    print("ok")
