#!/usr/bin/env python3
"""Position-management ladder — turns the deterministic risk + deterioration read into ONE convergent
action per holding: ADD / HOLD / TRIM to N sh / EXIT, honoring the concentration cap and the core-hold
exemption. Targets are absolute (shares of the baseline), so a repeated signal converges — no infinite
"trim 25% of current" regress. Pure logic; no LLM, no I/O. Caller passes one consistent currency.
"""
from __future__ import annotations

from deterioration import score_to_stage

DEFAULT_CAP = 0.20      # max single tactical position as a fraction of portfolio value
TRIM_TOL = 0.05         # don't nag a trim within 5% of the target
STAGE_TARGET = {0: 100, 1: 75, 2: 50, 3: 0}   # % of baseline qty per scale-out stage


def _res(action, target_qty, qty, reason, *, stage, pos_pct):
    return {
        "action": action,
        "target_qty": round(target_qty, 4),
        "delta_qty": round(target_qty - qty, 4),     # + = buy, - = sell
        "reason": reason,
        "stage": stage,
        "pos_pct": round(pos_pct * 100, 1),
    }


def recommend(*, qty: float, price: float, portfolio_value: float, is_core: bool,
              det_score: int, stop_hit: bool, max_stage: int, baseline_qty: float,
              has_entry_event: bool, cap: float = DEFAULT_CAP) -> dict:
    pos_pct = (qty * price) / portfolio_value if portfolio_value else 0.0
    cap_qty = (cap * portfolio_value / price) if price else qty

    if is_core:
        return _res("HOLD", qty, qty, "core hold — exempt from scale-out & cap", stage=0, pos_pct=pos_pct)

    stage, _ = score_to_stage(det_score, stop_hit)
    stage = max(stage, max_stage)                        # ratchet down only
    det_ceiling = baseline_qty * STAGE_TARGET[stage] / 100.0
    ceiling = min(det_ceiling, cap_qty)                  # binding upper limit

    if stage >= 3:
        return _res("EXIT", 0, qty, "stop hit / exit score ≥7 — exit fully", stage=stage, pos_pct=pos_pct)

    if qty > ceiling * (1 + TRIM_TOL):
        why = "deterioration" if det_ceiling <= cap_qty else "over concentration cap"
        return _res(f"TRIM to {round(ceiling)}", ceiling, qty,
                    f"{why}: reduce to {round(ceiling)} sh (stage {stage}, {cap*100:.0f}% cap)",
                    stage=stage, pos_pct=pos_pct)

    half_cap = cap_qty * 0.5   # build toward a HALF (~10%) position on a single setup, not the full cap
    if det_score == 0 and has_entry_event and qty < half_cap * 0.9:
        return _res(f"ADD toward {round(half_cap)}", half_cap, qty,
                    "clean (0/9) + fresh entry setup, below half-cap — add toward a half position",
                    stage=0, pos_pct=pos_pct)

    return _res("HOLD", qty, qty, "at/below target, no deterioration", stage=stage, pos_pct=pos_pct)


if __name__ == "__main__":  # self-test
    PV = 100_000

    # 1) core hold -> always HOLD even with deterioration
    r = recommend(qty=200, price=75, portfolio_value=PV, is_core=True, det_score=6, stop_hit=False,
                  max_stage=2, baseline_qty=200, has_entry_event=False)
    assert r["action"] == "HOLD" and r["delta_qty"] == 0

    # 2) deterioration stage 2 -> trim to 50% of baseline
    r = recommend(qty=100, price=50, portfolio_value=PV, is_core=False, det_score=5, stop_hit=False,
                  max_stage=0, baseline_qty=100, has_entry_event=False)
    assert r["action"] == "TRIM to 50" and r["delta_qty"] == -50

    # 3) already AT the stage-2 target -> HOLD, no re-trim (convergence property)
    r = recommend(qty=50, price=50, portfolio_value=PV, is_core=False, det_score=5, stop_hit=False,
                  max_stage=2, baseline_qty=100, has_entry_event=False)
    assert r["action"] == "HOLD", r

    # 4) over the 20% cap while healthy -> trim toward cap
    r = recommend(qty=1000, price=30, portfolio_value=PV, is_core=False, det_score=1, stop_hit=False,
                  max_stage=0, baseline_qty=1000, has_entry_event=False)   # 30% of PV
    assert r["action"].startswith("TRIM") and "concentration" in r["reason"]
    assert abs(r["target_qty"] - 0.20 * PV / 30) < 1

    # 5) hard stop -> exit
    r = recommend(qty=100, price=40, portfolio_value=PV, is_core=False, det_score=2, stop_hit=True,
                  max_stage=0, baseline_qty=100, has_entry_event=False)
    assert r["action"] == "EXIT" and r["target_qty"] == 0

    # 6) healthy + fresh entry event + far below cap -> add toward cap
    r = recommend(qty=20, price=50, portfolio_value=PV, is_core=False, det_score=0, stop_hit=False,
                  max_stage=0, baseline_qty=20, has_entry_event=True)
    assert r["action"].startswith("ADD") and r["delta_qty"] > 0

    print("[self-test OK]")
