# Thesis Diagnostic Framework

> Generated from GPT 5.5 extended thinking session + Hermes extrapolation, 2026-05-21.
> This framework should be applied to every edge thesis before it enters the experiment queue.

## 7 Diagnostic Questions

Every edge thesis must answer all 7 questions. If any answer is "unknown" or hand-wavy, the thesis is not ready for baseline testing.

| # | Question | What it reveals |
|---|----------|-----------------|
| 1 | **Who is paying me?** | Identifies the structural source. Without this, you don't know if the edge is real or a data-mined artifact. |
| 2 | **Am I providing liquidity, taking trend risk, selling insurance, or exploiting forced flow?** | Prevents duplicate edges. Two "trend risk" edges will die together in ranging markets. |
| 3 | **What market condition kills this?** | Reveals hidden correlation. If two edges die in the same condition, they are NOT diversified. |
| 4 | **Does it make money by many small wins, few large wins, or carry?** | Determines portfolio sizing. Many-small-wins edges need high win-rate discipline. Few-large-wins edges need patience through losing streaks. Carry edges need crash protection. |
| 5 | **Does it love volatility or hate volatility?** | Key for stacking. Pair opposite volatility preferences. |
| 6 | **Does it need fast execution?** | Determines whether automation actually helps. Slow edges work manually. Fast edges (intraday, opening range) require automation to capture. |
| 7 | **Is the stop part of the edge or just emotional comfort?** | Critical for position sizing. Structural stops = size for the stop distance. Cosmetic stops = size for max adverse excursion, not the stop level. |

## Stop-Is-Structural Classification

This attribute should be added to every edge thesis. It changes how the strategy is sized and evaluated.

### Structural stop (true)

The stop IS the edge. If price hits the stop, the thesis is falsified.

Examples:
- **Trend pullback**: stop below the SMA confirms trend is broken. The edge IS directional persistence.
- **Range expansion breakout**: stop inside the compression range confirms breakout failed.
- **Opening range momentum**: stop back inside opening range confirms no follow-through.

Position sizing: size for the stop distance. Risk per trade = stop distance × position size.

### Cosmetic stop (false)

The stop is risk management, not edge logic. The edge is the reversion/mean-reversion behavior, and price may overshoot before reverting.

Examples:
- **Failed-breakout reversal**: the edge IS the reversion. A tight stop guarantees you get stopped out of winning trades during the overshoot phase.
- **VWAP extreme reversion**: VWAP is a magnet, not a wall. Price can extend 3σ before reverting to 2σ.
- **Daily liquidity reversal**: capitulation can extend further before the reclaim.

Position sizing: size for the max adverse excursion (not the stop). Use wider stops or no hard stop, with time-based exit. Most mean-reversion systems die from badly placed tight stops.

### Decision tree

```
Is the edge fundamentally about directional persistence?
  YES → Stop IS structural → size for stop distance
  NO  → Is the edge fundamentally about reversion to a mean/level?
           YES → Stop is cosmetic → size for max adverse excursion
           NO  → Is the edge fundamentally about time decay (carry/premium)?
                    YES → Stop is cosmetic → size for crash scenario, not stop
```

## Stack Construction Principles

### Golden rule: test the most orthogonal pair first

Before testing all edges, test the most anti-correlated pair. If their combined equity curve is smoother than either alone, the structural separation thesis is confirmed. Only then add a third edge.

Why: if the pair doesn't diversify, stacking 6 edges won't help. You've just built 6 copies of the same bet.

### Layering model

```
Layer 1: Directional core      → trend pullback          (freq: daily, wins: few large)
Layer 2: Reversion diversifier  → failed-breakout reversal (freq: intraday, wins: many small)
Layer 3: Intraday flow          → opening range            (freq: daily per session)
Layer 4: Event-driven sparse    → capitulation + calendar  (freq: monthly)
Layer 5: Premium capture        → carry + short vol        (freq: continuous)
```

Each layer draws from a different structural source. If two edges are in the same layer, pick one — don't stack both without proving they're anti-correlated.

### Correlation cheat sheet

| Edge pair | Correlation | Why |
|-----------|------------|-----|
| Trend pullback + FBR | Negative | Range-bound markets kill trend, feed FBR. Trending markets kill FBR, feed trend. |
| Trend pullback + opening range | Positive (moderate) | Both directional. Different timeframes reduce correlation but don't eliminate it. |
| FBR + VWAP reversion | Positive (high) | Same structural source (liquidity provision). Same market condition kills both. |
| FBR + range expansion | Negative | FBR wins in compression, loses on expansion. Range expansion is the opposite. |
| Daily capitulation + trend pullback | Negative | Capitulation is anti-trend by definition. |
| Carry + all directional edges | Near-zero | Time premium is structurally orthogonal to price direction. |
| Calendar + all others | Near-zero | Calendar effects are flow-based, not price-based. |

### Regime rotation overlay

A meta-strategy that allocates capital based on which regime is active, without requiring new strategy code:

```
Regime detection → capital allocation

ADX > 25 + SMA slope positive:
  → 70% trend pullback, 20% carry, 10% calendar
  → 0% FBR, 0% VWAP reversion, 0% range expansion

ADX < 20, price in range:
  → 50% FBR, 30% VWAP reversion, 20% calendar
  → 0% trend pullback, 0% range expansion

ADX rising from <15 to >25 (expansion):
  → 50% range expansion breakout, 30% trend pullback, 20% carry
  → 0% FBR, 0% VWAP reversion

Post-crash (D1 drop > 2x ATR, prior low sweep):
  → 60% daily capitulation, 20% overnight gap fade, 20% calendar
  → 0% trend pullback, 0% FBR

Pre-scheduled event (FOMC, NFP within 48h):
  → 40% pre-event drift, 30% carry, 30% calendar
  → 0% directional, 0% reversion

Session open (first 2 hours):
  → 50% opening range, 30% VWAP reversion, 20% overnight gap fade
  → 0% swing/positional edges
```

## Integration with Research OS

### Quality gate checklist

Before an edge thesis enters the experiment queue, verify:

- [ ] All 7 diagnostic questions answered with specificity (not "it depends")
- [ ] `stop_is_structural: true/false` set on thesis
- [ ] Structural source (`who_pays`) field populated
- [ ] Killer condition (`what_kills_this`) field populated
- [ ] `correlation_risk` field lists specific edges this one correlates with
- [ ] `volatility_preference: loves | hates | neutral` field populated
- [ ] `payout_profile: many_small_wins | few_large_wins | carry | premium` field populated
- [ ] `execution_speed: immediate | same_session | same_day | multi_day` field populated
- [ ] Stack layer assignment (`layer: 1-5`) populated

### Schema extensions needed

The edge thesis schema (contracts.py EDGE_REQUIRED_FIELDS) should gain:

```python
OPTIONAL_DIAGNOSTIC_FIELDS = {
    "who_pays",
    "what_kills_this",
    "payout_profile",
    "volatility_preference",
    "execution_speed",
    "stop_is_structural",
    "stack_layer",
    "correlation_risk",
}
```

These are optional for backwards compatibility but required for the quality gate.

## Anti-patterns

- **Edge without structural source**: "RSI < 30 buy, RSI > 70 sell" without explaining WHO is on the other side. This is just parameter-fitting.
- **Stack of same-source edges**: 3 mean-reversion edges. You haven't diversified — you've tripled your exposure to the condition that kills mean reversion.
- **Tight stops on mean-reversion edges**: The edge IS the reversion. A tight stop guarantees you exit at max pain before the reversion happens.
- **Stacking without correlation testing**: Adding edges incrementally without checking whether Edge 3 actually improves Edge 1+2 combined equity curve.
