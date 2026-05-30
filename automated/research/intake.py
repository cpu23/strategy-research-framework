from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import implementation as impl_mod, registry
from .hashing import file_sha256
from .schemas import (
    GENERATED_SPECS_DIR,
    HYPOTHESES_DIR,
    REPO_ROOT,
    SANDBOX_ROOT,
    SchemaValidationError,
    load_yaml,
    resolve_hypothesis_file,
    validate_hypothesis,
    validate_strategy_spec,
)

HYPOTHESIS_TEMPLATES: dict[str, dict[str, dict[str, Any]]] = {
    "mean_reversion": {
        "ranging": {
            "name": "Mean reversion from range extremes in ranging market",
            "mechanism": (
                "In a ranging market, price tends to revert toward the range midpoint "
                "after touching extreme levels. Oversold/overbought conditions on "
                "oscillators at range boundaries provide entry signals."
            ),
            "expected_edge": (
                "Positive expectancy when reversion entries are taken at visible "
                "support/resistance levels with low ADX confirmation."
            ),
            "predictions": [
                "Should work better when ADX < 25",
                "Should work better when RSI is beyond 30/70 levels",
                "Should degrade during strong trend expansion",
            ],
            "failure_modes": [
                "Trend onset traps reversion entries",
                "Range boundaries break into genuine breakout",
                "Edge concentrates in one asset or volatility regime",
            ],
            "entry_idea": "RSI < 30 (long) or RSI > 70 (short) at recent range boundary with ADX < 25",
            "exit_idea": "Range midpoint take-profit or 1.5x ATR trailing stop",
            "risk_idea": "ATR-based stop at 1.5x ATR beyond range boundary; 1% risk per trade",
            "required_inputs": [
                {"name": "InpRsiPeriod", "type": "int", "required": True, "default": "14"},
                {"name": "InpRsiOversold", "type": "int", "required": True, "default": "30"},
                {"name": "InpRsiOverbought", "type": "int", "required": True, "default": "70"},
                {"name": "InpRangeLookbackBars", "type": "int", "required": True, "default": "20"},
            ],
        },
        "trending": {
            "name": "Mean reversion within trend pullback",
            "mechanism": (
                "In a trending market, price pulls back to a moving average or "
                "prior swing level before continuing in the trend direction. "
                "Reversion entries aligned with the dominant trend capture pullback moves."
            ),
            "expected_edge": (
                "Positive expectancy when reversion entries align with the larger "
                "trend direction during low-volatility pullbacks."
            ),
            "predictions": [
                "Should work better when ADX > 25 (trend confirmed)",
                "Should work better on pullbacks to key moving average",
                "Should degrade when pullback depth exceeds 2x ATR",
            ],
            "failure_modes": [
                "Trend reversal catches pullback entries",
                "Pullback depth is too shallow for viable risk/reward",
                "Edge depends on exact MA period",
            ],
            "entry_idea": "Price pulls back to 20 EMA with ADX > 25, enter in trend direction",
            "exit_idea": "Prior swing high/low or 2x ATR trailing stop",
            "risk_idea": "ATR-based stop at 1.5x ATR beyond pullback extreme; 1% risk per trade",
            "required_inputs": [
                {"name": "InpMaPeriod", "type": "int", "required": True, "default": "20"},
                {"name": "InpMaType", "type": "int", "required": True, "default": "1"},
            ],
        },
    },
    "breakout_continuation": {
        "ranging": {
            "name": "Breakout continuation from range compression",
            "mechanism": (
                "Following a period of compression inside a defined range, "
                "a decisive breakout with expanding volatility signals the start "
                "of a new directional move. Early continuation entries capture the expansion."
            ),
            "expected_edge": (
                "Positive expectancy when breakouts from narrow ranges are "
                "accompanied by ATR expansion and above-average volume."
            ),
            "predictions": [
                "Should work better when prior range is narrow (low ATR percentile)",
                "Should work better with above-average tick volume on breakout",
                "Should degrade on false breakouts that immediately reverse",
            ],
            "failure_modes": [
                "Breakout reverses immediately (failed breakout)",
                "Range is not meaningful to market participants",
                "Edge is concentrated in news-driven events",
            ],
            "entry_idea": "Price breaks 20-bar high/low with ATR expansion > 1.2x median",
            "exit_idea": "Trailing stop at 2x ATR or prior swing high/low",
            "risk_idea": "ATR-based stop at 1x ATR inside range; 1% risk per trade",
            "required_inputs": [
                {"name": "InpBreakoutLookback", "type": "int", "required": True, "default": "20"},
                {"name": "InpAtrExpansionFactor", "type": "double", "required": True, "default": "1.2"},
            ],
        },
        "trending": {
            "name": "Trend continuation after consolidation",
            "mechanism": (
                "During an established trend, price often consolidates sideways "
                "before continuing in the trend direction. Entries on the "
                "resumption of trend momentum capture the continuation phase."
            ),
            "expected_edge": (
                "Positive expectancy when entering on trend resumption after "
                "a consolidation period during a strong trend."
            ),
            "predictions": [
                "Should work better when ADX > 25 and rising",
                "Should work better after consolidation of 5-15 bars",
                "Should degrade when ADX crosses below 25",
            ],
            "failure_modes": [
                "Consolidation becomes a reversal pattern",
                "Trend exhausts after the continuation entry",
                "Edge requires subjective consolidation identification",
            ],
            "entry_idea": "Price breaks above/below consolidation range during ADX > 25 trend",
            "exit_idea": "Trailing stop at 2.5x ATR or prior swing level",
            "risk_idea": "ATR-based stop at 1.5x ATR beyond consolidation; 1% risk per trade",
            "required_inputs": [
                {"name": "InpConsolidationLookback", "type": "int", "required": True, "default": "10"},
                {"name": "InpMinAdx", "type": "int", "required": True, "default": "25"},
            ],
        },
    },
    "failed_breakout_reversal": {
        "ranging": {
            "name": "Failed breakout reversal toward range midpoint",
            "mechanism": (
                "In a ranging market, a break beyond a prior range boundary "
                "can trap breakout participants when price closes back inside "
                "the range. Trapped flow supports reversal toward range midpoint."
            ),
            "expected_edge": (
                "Positive expectancy after failed breaks of prior range extremes "
                "when ADX is low or falling and ATR is not in extreme expansion."
            ),
            "predictions": [
                "Should work better when ADX < 25",
                "Should work better when ATR percentile < 80",
                "Should improve with moderate breakout distance (0.1-0.5 ATR)",
                "Should degrade in genuine trend expansion",
            ],
            "failure_modes": [
                "Real breakout continues through failed-break entry",
                "Range boundaries are not meaningful to participants",
                "Edge is concentrated in one asset or volatility regime",
            ],
            "entry_idea": "Price breaks 20-bar high/low, closes back inside, ADX < 25",
            "exit_idea": "Range midpoint take-profit or ATR stop at 1.5x beyond boundary",
            "risk_idea": "ATR-based stop at 1x beyond range boundary; 1% risk per trade",
            "required_inputs": [
                {"name": "InpRangeLookbackBars", "type": "int", "required": True, "default": "20"},
                {"name": "InpMaxAdx", "type": "int", "required": True, "default": "25"},
                {"name": "InpMaxBreakDistanceAtr", "type": "double", "required": True, "default": "0.5"},
            ],
        },
        "trending": {
            "name": "Failed breakout reversal against trend",
            "mechanism": (
                "In a trending market, a breakout beyond a prior swing extreme "
                "that immediately reverses can indicate trend exhaustion. "
                "Counter-trend entries capture the reversal, with tighter risk."
            ),
            "expected_edge": (
                "Positive expectancy when a trend-extreme breakout fails and "
                "reverses with above-average momentum on the reversal bar."
            ),
            "predictions": [
                "Should work better when ADX is very high (> 40) indicating trend climax",
                "Should work better when breakout bar has large range ( > 1.5x ATR)",
                "Should degrade when trend resumes after the failed break",
            ],
            "failure_modes": [
                "Trend resumes immediately after the failed break entry",
                "Counter-trend entries in strong trends have poor risk/reward",
                "Edge is concentrated in specific chart patterns",
            ],
            "entry_idea": "Price breaks recent swing high/low, reverses with ADX > 40, enter counter-trend",
            "exit_idea": "Prior swing level or 1.5x ATR take-profit; tight stop at breakout extreme",
            "risk_idea": "ATR-based stop at 1x ATR beyond breakout extreme; 0.5% risk per trade",
            "required_inputs": [
                {"name": "InpMinAdx", "type": "int", "required": True, "default": "40"},
                {"name": "InpSwingLookback", "type": "int", "required": True, "default": "10"},
            ],
        },
    },
}

SHORT_FAMILY: dict[str, str] = {
    "mean_reversion": "MR",
    "breakout_continuation": "BC",
    "failed_breakout_reversal": "FBR",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _generate_hypothesis_id(index: int, strategy_family: str, market_regime: str) -> str:
    short = SHORT_FAMILY.get(strategy_family, strategy_family.upper()[:4])
    regime = market_regime.upper()[:7]
    return f"HYP_GEN_{short}_{regime}_{index:03d}"


def _build_hypothesis(
    template: dict[str, Any],
    hypothesis_id: str,
    index: int,
    strategy_family: str,
    market_regime: str,
    symbol: str,
    timeframe: str,
    created_by: str,
) -> dict[str, Any]:
    now_stamp = utc_now()
    name = template["name"]
    mechanism = template["mechanism"]
    expected_edge = template["expected_edge"]
    entry_idea = template["entry_idea"]
    exit_idea = template["exit_idea"]
    risk_idea = template["risk_idea"]
    invalidation_rule = (
        f"Reject {name} if baseline testing cannot produce positive expectancy "
        f"across {symbol} {timeframe} with standard risk controls."
    )
    initial_test = (
        f"Baseline test on {symbol} {timeframe} using {entry_idea} "
        f"with {exit_idea} and {risk_idea}."
    )

    hypothesis: dict[str, Any] = {
        "hypothesis_id": hypothesis_id,
        "name": name,
        "status": "active_research",
        "mechanism": mechanism,
        "expected_edge": expected_edge,
        "timeframes": [timeframe],
        "markets": [symbol],
        "predictions": list(template["predictions"]),
        "failure_modes": list(template["failure_modes"]),
        "initial_test": initial_test,
        "invalidation_rule": invalidation_rule,
        "created_at": now_stamp.split("T")[0],
        "updated_at": now_stamp.split("T")[0],
        "_phase10": {
            "generated": True,
            "index": index,
            "strategy_family": strategy_family,
            "market_regime_assumption": market_regime,
            "entry_idea": entry_idea,
            "exit_idea": exit_idea,
            "risk_idea": risk_idea,
            "invalidation_criteria": invalidation_rule,
            "expected_failure_modes": list(template["failure_modes"]),
            "required_inputs": list(template["required_inputs"]),
            "created_by": created_by,
            "created_at": now_stamp,
        },
    }
    return hypothesis


def generate_hypotheses(
    *,
    research_theme: str = "",
    symbol: str = "",
    timeframe: str = "",
    market_regime: str = "",
    strategy_family: str = "",
    max_hypotheses: int = 1,
    constraints: dict[str, Any] | None = None,
    created_by: str = "intake_agent",
    hypothesis_set_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    if strategy_family not in HYPOTHESIS_TEMPLATES:
        raise ValueError(
            f"Unknown strategy_family: {strategy_family!r}. "
            f"Supported: {sorted(HYPOTHESIS_TEMPLATES)}"
        )
    if market_regime not in HYPOTHESIS_TEMPLATES[strategy_family]:
        raise ValueError(
            f"Unknown market_regime {market_regime!r} for {strategy_family}. "
            f"Supported: {sorted(HYPOTHESIS_TEMPLATES[strategy_family])}"
        )

    if max_hypotheses < 1:
        raise ValueError("max_hypotheses must be >= 1")

    template_pool = HYPOTHESIS_TEMPLATES[strategy_family][market_regime]
    num_generated = min(max_hypotheses, 1)

    hypotheses: list[dict[str, Any]] = []
    for i in range(num_generated):
        hyp_id = _generate_hypothesis_id(i, strategy_family, market_regime)
        hypothesis = _build_hypothesis(
            template=template_pool,
            hypothesis_id=hyp_id,
            index=i,
            strategy_family=strategy_family,
            market_regime=market_regime,
            symbol=symbol,
            timeframe=timeframe,
            created_by=created_by,
        )
        hyp_path = HYPOTHESES_DIR / f"{hyp_id}.yaml"
        check_result = impl_mod.check_overwrite(hyp_path)
        if check_result:
            raise FileExistsError(
                f"{check_result} (delete it or use a different hypothesis_id range)"
            )
        hyp_path.parent.mkdir(parents=True, exist_ok=True)
        hyp_path.write_text(yaml.safe_dump(hypothesis, sort_keys=False), encoding="utf-8")
        hypotheses.append({"path": str(hyp_path), **hypothesis})

    if hypothesis_set_dir:
        set_dir = Path(hypothesis_set_dir)
        set_dir.mkdir(parents=True, exist_ok=True)
        set_path = set_dir / "hypothesis_set.yaml"
        set_doc = {
            "artifact_type": "hypothesis_set",
            "research_theme": research_theme,
            "symbol": symbol,
            "timeframe": timeframe,
            "market_regime": market_regime,
            "strategy_family": strategy_family,
            "max_hypotheses": max_hypotheses,
            "generated_count": len(hypotheses),
            "hypotheses": [
                {
                    "hypothesis_id": h["hypothesis_id"],
                    "title": h["name"],
                    "path": h["path"],
                }
                for h in hypotheses
            ],
            "created_by": created_by,
            "created_at": utc_now(),
        }
        set_path.write_text(yaml.safe_dump(set_doc, sort_keys=False), encoding="utf-8")
        for h in hypotheses:
            h["hypothesis_set_path"] = str(set_path)

    return hypotheses


def generate_strategy_spec(
    *,
    hypothesis_id: str,
    strategy_id: str,
    strategy_version: str = "v1",
    created_by: str = "intake_agent",
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    hyp_path = resolve_hypothesis_file(hypothesis_id)
    if not hyp_path:
        raise FileNotFoundError(f"hypothesis not found: {hypothesis_id}")
    hypothesis = load_yaml(hyp_path)
    validate_hypothesis(hypothesis)

    phase10 = hypothesis.get("_phase10", {})
    entry_idea = phase10.get("entry_idea", hypothesis.get("mechanism", ""))
    exit_idea = phase10.get("exit_idea", "Trailing stop or fixed target")
    risk_idea = phase10.get("risk_idea", "ATR-based stop, 1% risk per trade")
    required_inputs = phase10.get("required_inputs", [])
    markets = hypothesis.get("markets", ["XAUUSD"])
    timeframes_list = hypothesis.get("timeframes", ["H4"])
    timeframe = timeframes_list[0] if timeframes_list else "H4"
    symbol = markets[0] if markets else "XAUUSD"

    parameters: dict[str, Any] = {}
    for inp in required_inputs:
        if inp.get("default") is not None:
            raw = inp["default"]
            typed: Any = raw
            if inp["type"] == "int":
                try:
                    typed = int(raw)
                except (ValueError, TypeError):
                    pass
            elif inp["type"] == "double":
                try:
                    typed = float(raw)
                except (ValueError, TypeError):
                    pass
            elif inp["type"] == "bool":
                typed = raw.lower() in ("true", "1", "yes")
            parameters[inp["name"]] = typed

    parameters.setdefault("InpMagicNumber", 12345)
    parameters.setdefault("InpRiskPercent", 1.0)
    parameters.setdefault("InpStopLossAtr", 15)
    parameters.setdefault("InpTakeProfitAtr", 30)
    parameters.setdefault("InpAtrPeriod", 14)

    expected_inputs: list[dict[str, Any]] = list(required_inputs)
    extra_inputs = [
        {"name": "InpMagicNumber", "type": "int", "required": True, "default": "12345"},
        {"name": "InpSymbol", "type": "string", "required": False, "default": ""},
        {"name": "InpRiskPercent", "type": "double", "required": True, "default": "1.0"},
        {"name": "InpStopLossAtr", "type": "int", "required": True, "default": "15"},
        {"name": "InpTakeProfitAtr", "type": "int", "required": True, "default": "30"},
        {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
    ]
    existing_names = {e["name"] for e in expected_inputs}
    for extra in extra_inputs:
        if extra["name"] not in existing_names:
            expected_inputs.append(extra)
            existing_names.add(extra["name"])

    now_stamp = utc_now()
    mq5_filename = f"{strategy_id}.mq5"
    sandbox_mq5_path = f"automated/generated_strategies/{strategy_id}/{strategy_version}/{mq5_filename}"

    spec: dict[str, Any] = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "hypothesis_id": hypothesis_id,
        "status": "idea",
        "created_at": now_stamp.split("T")[0],
        "updated_at": now_stamp.split("T")[0],
        "universe": [symbol],
        "timeframe": timeframe,
        "implementation": {
            "engine": "mt5",
            "generation_mode": "wrapped_existing_files",
            "files": {
                "config": f"automated/runs/{strategy_id}_baseline.conf",
                "parameters": f"automated/runs/sets/{strategy_id}_baseline.set",
                "expert_advisor": sandbox_mq5_path,
            },
        },
        "execution_timing": {
            "signal_bar": "closed_bar",
            "entry_bar": "next_bar",
            "assumed_fill_price": "market_first_tick_or_tester_fill_at_next_bar",
        },
        "costs": {
            "assumptions_documented": True,
            "spread_source": {"type": "mt5_tester", "description": "Broker/tester spread model."},
            "slippage": {"type": "points", "value": 20, "source": "InpSlippagePoints"},
            "commission": {"type": "broker_account_or_tester_default", "value": None, "description": "No explicit commission override."},
            "stress_multiplier": None,
        },
        "entry": {
            "type": strategy_family_from_spec(strategy_id, hypothesis),
            "description": entry_idea,
            "parameters": {k: v for k, v in parameters.items() if k.startswith("Inp") and k not in ("InpMagicNumber", "InpRiskPercent", "InpStopLossAtr", "InpTakeProfitAtr", "InpAtrPeriod")},
        },
        "regime_filters": [
            {"type": "adx_below_threshold", "parameter": "max_adx"} if "max_adx" in parameters or "InpMaxAdx" in parameters else {"type": "none", "parameter": None},
        ],
        "exit": {
            "type": "atr_based_or_trailing",
            "rules": ["atr_stop_loss", "atr_take_profit"],
            "description": exit_idea,
        },
        "risk": {
            "position_sizing": "risk_percent",
            "parameters": {
                "risk_percent": 1.0,
                "atr_stop_multiplier": 1.5,
                "atr_target_multiplier": 2.0,
            },
        },
        "validation": {
            "min_trades_required": 30,
            "warning_thresholds": {
                "max_drawdown_pct": 30.0,
                "min_profit_factor": 1.2,
            },
        },
        "research_budget": {
            "max_structural_variants": 3,
            "max_parameter_sets": 5,
            "max_filter_additions": 3,
            "max_agent_iterations": 2,
            "max_complexity_score": 30,
        },
        "lifecycle": {
            "state": "idea",
            "allowed_next_states": ["hypothesis_defined"],
        },
        "invalidation_rule": hypothesis.get("invalidation_rule", "Reject if baseline tests fail."),
        "parameters": parameters,
        "implementation_key_inventory": {},
        "sweepable_parameters": {},
    }

    validate_strategy_spec(spec, require_files=False)

    spec_dir = output_dir if output_dir else GENERATED_SPECS_DIR
    spec_dir_path = Path(spec_dir)
    spec_dir_path.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir_path / f"{strategy_id}.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    return {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "spec_path": str(spec_path),
        "sandbox_mq5_path": sandbox_mq5_path,
        "expected_inputs": expected_inputs,
        "parameters": parameters,
    }


def strategy_family_from_spec(strategy_id: str, hypothesis: dict[str, Any]) -> str:
    phase10 = hypothesis.get("_phase10", {})
    family = phase10.get("strategy_family", "unknown")
    readable = family.replace("_", " ").title()
    return readable


def _build_generated_mq5(
    strategy_id: str,
    strategy_version: str,
    expected_inputs: list[dict[str, Any]],
    sandbox_dir: Path,
) -> Path:
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    mq5_path = sandbox_dir / f"{strategy_id}.mq5"

    lines: list[str] = []
    lines.append(f"//+------------------------------------------------------------------+")
    lines.append(f"//| {strategy_id}.mq5")
    lines.append(f"//| Generated by Research OS Phase 10 Intake")
    lines.append(f"//+------------------------------------------------------------------+")
    lines.append(f"#property copyright \"Research OS\"")
    lines.append(f"#property link      \"\"")
    lines.append(f"#property version   \"1.00\"")
    lines.append(f"")

    lines.append(f"#include <Trade/Trade.mqh>")
    lines.append(f"")

    for inp in expected_inputs:
        dtype = inp.get("type", "int")
        name = inp["name"]
        default = inp.get("default", "")
        if default is not None and default != "":
            lines.append(f"input {dtype}   {name} = {default};")
        else:
            lines.append(f"input {dtype}   {name};")

    lines.append(f"")
    lines.append(f"CTrade  trade;")
    lines.append(f"int     atrHandle;")
    lines.append(f"double  atrBuffer[];")
    lines.append(f"")

    lines.append(f"int OnInit()")
    lines.append(f"{{")
    lines.append(f"   trade.SetExpertMagicNumber(InpMagicNumber);")
    lines.append(f"   atrHandle = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);")
    lines.append(f"   if (atrHandle == INVALID_HANDLE)")
    lines.append(f"      return INIT_FAILED;")
    lines.append(f"   ArraySetAsSeries(atrBuffer, true);")
    lines.append(f"   return INIT_SUCCEEDED;")
    lines.append(f"}}")
    lines.append(f"")

    lines.append(f"void OnDeinit(const int reason)")
    lines.append(f"{{")
    lines.append(f"   if (atrHandle != INVALID_HANDLE)")
    lines.append(f"      IndicatorRelease(atrHandle);")
    lines.append(f"}}")
    lines.append(f"")

    lines.append(f"void OnTick()")
    lines.append(f"{{")
    lines.append(f"   if (InpSymbol != \"\" && _Symbol != InpSymbol)")
    lines.append(f"      return;")
    lines.append(f"")
    lines.append(f"   static datetime lastBar = 0;")
    lines.append(f"   datetime currentBar = iTime(_Symbol, PERIOD_CURRENT, 0);")
    lines.append(f"   if (currentBar == lastBar)")
    lines.append(f"      return;")
    lines.append(f"   lastBar = currentBar;")
    lines.append(f"")
    lines.append(f"   if (CopyBuffer(atrHandle, 0, 0, 2, atrBuffer) < 2)")
    lines.append(f"      return;")
    lines.append(f"   double atr = atrBuffer[0];")
    lines.append(f"")
    lines.append(f"   if (PositionSelect(_Symbol))")
    lines.append(f"      return;")
    lines.append(f"")
    lines.append(f"   bool buySignal = false;")
    lines.append(f"   bool sellSignal = false;")
    lines.append(f"")
    lines.append(f"   if (buySignal)")
    lines.append(f"   {{")
    lines.append(f"      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);")
    lines.append(f"      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);")
    lines.append(f"      double sl = NormalizeDouble(bid - atr * InpStopLossAtr / 10.0, _Digits);")
    lines.append(f"      double tp = NormalizeDouble(ask + atr * InpTakeProfitAtr / 10.0, _Digits);")
    lines.append(f"      double riskAmount = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;")
    lines.append(f"      double slDistance = atr * InpStopLossAtr / 10.0;")
    lines.append(f"      double lot = NormalizeDouble(riskAmount / slDistance, 2);")
    lines.append(f"      if (lot < SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))")
    lines.append(f"         lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);")
    lines.append(f"      trade.Buy(lot, _Symbol, 0, sl, tp);")
    lines.append(f"   }}")
    lines.append(f"")
    lines.append(f"   if (sellSignal)")
    lines.append(f"   {{")
    lines.append(f"      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);")
    lines.append(f"      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);")
    lines.append(f"      double sl = NormalizeDouble(ask + atr * InpStopLossAtr / 10.0, _Digits);")
    lines.append(f"      double tp = NormalizeDouble(bid - atr * InpTakeProfitAtr / 10.0, _Digits);")
    lines.append(f"      double riskAmount = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;")
    lines.append(f"      double slDistance = atr * InpStopLossAtr / 10.0;")
    lines.append(f"      double lot = NormalizeDouble(riskAmount / slDistance, 2);")
    lines.append(f"      if (lot < SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))")
    lines.append(f"         lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);")
    lines.append(f"      trade.Sell(lot, _Symbol, 0, sl, tp);")
    lines.append(f"   }}")
    lines.append(f"}}")

    mq5_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mq5_path


def _format_set_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_generated_runner_files(spec: dict[str, Any], strategy_id: str, mq5_path: Path) -> dict[str, str]:
    implementation_files = spec["implementation"]["files"]
    config_path = REPO_ROOT / implementation_files["config"]
    parameters_path = REPO_ROOT / implementation_files["parameters"]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    parameters_path.parent.mkdir(parents=True, exist_ok=True)

    parameters = spec.get("parameters", {})
    set_lines = [f"{key}={_format_set_value(value)}" for key, value in sorted(parameters.items())]
    parameters_path.write_text("\n".join(set_lines) + "\n", encoding="utf-8")

    universe = spec.get("universe", ["XAUUSD"])
    symbol = universe[0] if universe else "XAUUSD"
    timeframe = spec.get("timeframe", "H4")
    ea_name = mq5_path.stem
    ea_source = mq5_path.relative_to(REPO_ROOT)
    mt5_expert = f"Automated\\\\{ea_name}"
    config_lines = [
        f'RUN_ID="{strategy_id}_baseline"',
        f'STRATEGY_ID="{strategy_id}"',
        f'EA_NAME="{ea_name}"',
        f'EA_SOURCE="{ea_source.as_posix()}"',
        f'MT5_EXPERT="{mt5_expert}"',
        'BROKER="mock"',
        f'SYMBOL="{symbol}"',
        f'TIMEFRAME="{timeframe}"',
        'DATE_FROM="2024.01.01"',
        'DATE_TO="2025.12.31"',
        'DEPOSIT="100000"',
        'CURRENCY="USD"',
        'LEVERAGE="1:100"',
        'MODEL="1"',
        'EXECUTION_MODE="0"',
        'OPTIMIZATION="0"',
        'FORWARD_MODE="0"',
        'VISUAL="0"',
        'USE_LOCAL="1"',
        'USE_REMOTE="0"',
        'USE_CLOUD="0"',
        f'EA_SET_FILE="{parameters_path.resolve()}"',
    ]
    config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    return {"config_path": str(config_path), "parameters_path": str(parameters_path)}


def materialize_implementation(
    db_path: str | Path,
    *,
    strategy_spec_path: str | Path,
    strategy_id: str,
    strategy_version: str = "v1",
    created_by: str = "intake_agent",
    mock_compile: bool = True,
) -> dict[str, Any]:
    spec_rel = Path(strategy_spec_path)
    spec_abs = spec_rel if spec_rel.is_absolute() else REPO_ROOT / spec_rel
    spec = load_yaml(spec_abs)
    validate_strategy_spec(spec, require_files=False)

    parameters = spec.get("parameters", {})
    entry = spec.get("entry", {})
    entry_logic = entry.get("description", "") if isinstance(entry, dict) else str(entry)
    exit_section = spec.get("exit", {})
    exit_logic = exit_section.get("description", "") if isinstance(exit_section, dict) else str(exit_section)
    risk = spec.get("risk", {})
    risk_params = risk.get("parameters", {}) if isinstance(risk, dict) else {}
    risk_logic = f"Position sizing: {risk.get('position_sizing', 'unknown')}; {risk_params}"

    expected_inputs: list[dict[str, Any]] = []
    for key, val in parameters.items():
        if key.startswith("Inp"):
            dtype = "int"
            if isinstance(val, float):
                dtype = "double"
            elif isinstance(val, bool):
                dtype = "bool"
            elif isinstance(val, str):
                dtype = "string"
            expected_inputs.append({"name": key, "type": dtype, "required": True, "default": str(val)})

    universe = spec.get("universe", ["XAUUSD"])
    symbol = universe[0] if universe else "XAUUSD"

    sandbox_dir = SANDBOX_ROOT / strategy_id / strategy_version
    impl_check = impl_mod.check_overwrite(sandbox_dir)
    if impl_check:
        sandbox_dir.mkdir(parents=True, exist_ok=True)

    impl_req = impl_mod.create_implementation_request(
        db_path,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        sandbox_dir=sandbox_dir,
        generated_files=[f"{strategy_id}.mq5"],
        created_by=created_by,
        hypothesis_id=spec.get("hypothesis_id"),
        strategy_spec_path=str(spec_abs),
        expected_inputs=expected_inputs,
        parameters=parameters,
        entry_logic=entry_logic,
        exit_logic=exit_logic,
        risk_logic=risk_logic,
    )
    impl_req_id = impl_req["implementation_request_id"]

    validate_outcome = impl_mod.validate_request(db_path, impl_req_id)
    if not validate_outcome["valid"]:
        return {
            "implementation_request_id": impl_req_id,
            "status": "failed",
            "errors": validate_outcome["errors"],
        }

    mq5_path = _build_generated_mq5(strategy_id, strategy_version, expected_inputs, sandbox_dir)
    runner_files = _write_generated_runner_files(spec, strategy_id, mq5_path)

    sandbox_files = [str(mq5_path)]

    compile_outcome = impl_mod.compile_check(db_path, impl_req_id, mock=mock_compile)
    compile_status = compile_outcome.get("compile_status", "unknown")

    diff_review_outcome = {}
    diff_review_status = "not_run"
    if compile_status in ("mock_checked", "passed"):
        diff_review_outcome = impl_mod.run_diff_review(db_path, impl_req_id)
        diff_review_status = "reviewed"
        diff_review_path = diff_review_outcome.get("artifact_path", "")

    return {
        "implementation_request_id": impl_req_id,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "sandbox_dir": str(sandbox_dir),
        "sandbox_files": sandbox_files,
        "mq5_path": str(mq5_path),
        "config_path": runner_files["config_path"],
        "parameters_path": runner_files["parameters_path"],
        "compile_status": compile_status,
        "diff_review_status": diff_review_status,
        "diff_review_path": diff_review_outcome.get("artifact_path", ""),
        "baseline_eligible": diff_review_outcome.get("baseline_eligible", False),
        "hard_blockers": diff_review_outcome.get("hard_blockers", []),
        "dangerous_patterns": diff_review_outcome.get("dangerous_patterns", []),
        "approved_for_baseline": False,
        "status": "materialized",
        "note": "Implementation materialized but NOT approved for baseline. Use 'implementation approve-for-baseline' to approve.",
    }


def build_review_packet(
    db_path: str | Path,
    *,
    strategy_id: str,
    strategy_version: str = "v1",
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    request = registry.find_implementation_request(db_path, strategy_id, strategy_version)
    implementations = []
    current_impl = None
    compile_status = None
    diff_review_artifact = None
    baseline_eligible = False
    input_match_status = None
    sandbox_path_str = None
    dangerous_patterns: list[dict[str, Any]] = []
    hard_blockers: list[str] = []
    hypothesis_id = None
    spec_path = None

    if request:
        spec_path = request.get("strategy_spec_path")
        sandbox_path_str = request.get("sandbox_dir")
        hypothesis_id = request.get("hypothesis_id")
        impl_records = registry.list_implementations(db_path, request["implementation_request_id"])
        if impl_records:
            current_impl = impl_records[-1]
            compile_status = current_impl.get("compile_status")
            input_match_status = current_impl.get("input_match_status")
            baseline_eligible = bool(
                current_impl.get("approved_for_baseline")
                and current_impl.get("compile_status") in ("passed", "mock_checked")
            )

        review_path_abs = Path(request["request_artifact_path"]).parent / "diff_review.yaml"
        if review_path_abs.is_file():
            import yaml as yl
            diff_review_artifact = str(review_path_abs)
            try:
                dr = yl.safe_load(review_path_abs.read_text(encoding="utf-8"))
                dangerous_patterns = dr.get("dangerous_patterns", [])
                hard_blockers = dr.get("hard_blockers", [])
                if dr.get("baseline_eligible") is not None:
                    baseline_eligible = dr["baseline_eligible"]
            except Exception:
                pass

    spec_path_abs = None
    hypothesis_summary: dict[str, Any] = {}
    if spec_path:
        spec_path_abs = Path(spec_path)
        if not spec_path_abs.is_absolute():
            spec_path_abs = REPO_ROOT / spec_path
        if spec_path_abs.is_file():
            try:
                spec_data = load_yaml(spec_path_abs)
                hyp_id_from_spec = spec_data.get("hypothesis_id", hypothesis_id)
                if hyp_id_from_spec:
                    hyp_file = resolve_hypothesis_file(hyp_id_from_spec)
                    if hyp_file:
                        hyp_data = load_yaml(hyp_file)
                        phase10 = hyp_data.get("_phase10", {})
                        hypothesis_summary = {
                            "hypothesis_id": hyp_data.get("hypothesis_id"),
                            "title": hyp_data.get("name"),
                            "symbol": hyp_data.get("markets", [None])[0],
                            "timeframe": hyp_data.get("timeframes", [None])[0],
                            "strategy_family": phase10.get("strategy_family"),
                            "market_regime_assumption": phase10.get("market_regime_assumption"),
                            "entry_idea": phase10.get("entry_idea"),
                            "exit_idea": phase10.get("exit_idea"),
                            "risk_idea": phase10.get("risk_idea"),
                        }
            except Exception:
                pass

    spec_path_str = str(spec_path_abs) if spec_path_abs else None
    generated_spec_dir = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    if not spec_path_str or not Path(spec_path_str).is_file():
        if generated_spec_dir.is_file():
            spec_path_str = str(generated_spec_dir)

    dangerous_warnings: list[str] = []
    for dp in dangerous_patterns:
        sev = dp.get("severity", "unknown")
        desc = dp.get("description", dp.get("id", "unknown"))
        dangerous_warnings.append(f"[{sev}] {desc}")

    approval_status = "not_approved"
    if current_impl and current_impl.get("approved_for_baseline"):
        approval_status = "approved_for_baseline"

    if hard_blockers:
        recommended_action = "revise_implementation"
    elif baseline_eligible and input_match_status == "match":
        recommended_action = "approve_for_one_baseline"
    elif compile_status not in ("passed", "mock_checked"):
        recommended_action = "revise_implementation"
    else:
        recommended_action = "defer"

    if recommended_action not in ("reject", "revise_implementation", "approve_for_one_baseline", "defer"):
        recommended_action = "defer"

    packet: dict[str, Any] = {
        "schema_version": "review_packet_v1",
        "review_packet_id": f"REVIEW_PACKET_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6].upper()}",
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "generated_at": utc_now(),
        "hypothesis_summary": hypothesis_summary,
        "strategy_spec_path": spec_path_str,
        "implementation_request_id": request["implementation_request_id"] if request else None,
        "sandbox_implementation_path": sandbox_path_str,
        "compile_status": compile_status,
        "input_spec_match_status": input_match_status,
        "diff_review_status": "reviewed" if diff_review_artifact else "not_run",
        "diff_review_path": diff_review_artifact,
        "dangerous_pattern_warnings": dangerous_warnings,
        "baseline_eligibility": baseline_eligible,
        "approval_status": approval_status,
        "recommended_next_action": recommended_action,
    }

    out_dir = Path(output_dir) if output_dir else Path(sandbox_path_str or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_path = out_dir / "review_packet.yaml"
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")

    return {
        "packet_path": str(packet_path),
        "packet": packet,
    }
