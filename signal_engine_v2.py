"""
signal_engine_v2.py
--------------------
Drop-in replacement for the flat "N of 7 indicators" scoring logic in
NSE_Bullish_Scanner (app.py).

WHY THIS EXISTS
Your current scanner treats EMA20/50, Supertrend, and MACD as three
*independent* votes, but all three are trend-following indicators built
from the same price series — they usually agree or disagree together.
Counting them separately inflates the apparent strength of a signal
(a stock can hit "6/7" mostly by being in one clean uptrend, not by
genuinely independent evidence).

This module:
  1. Groups indicators by what they actually measure (trend, momentum,
     participation) instead of counting them as 7 flat votes.
  2. Uses ADX purely as a *gate* (is there a trend at all?), not a vote.
  3. Adds a market-regime filter (NIFTY 50 above its own 50-EMA) so the
     scanner doesn't just surface "everything went up because the index
     went up" as if each stock had independent edge.
  4. Produces both a boolean "qualifies" flag and a 0-100 composite score
     for ranking, plus a breakdown you can render in the UI/detail panel.

HOW TO INTEGRATE
  - Keep your existing indicator calculation code (EMA, VWAP, RSI, ADX,
    Supertrend, MACD, Relative Volume) — this module expects those raw
    values as input, it doesn't recompute them from scratch except for
    the regime check.
  - Replace the place in app.py where you do something like:
        score = sum([cond1, cond2, ..., cond7])
        if score >= min_confirmations: ...
    with a call to `evaluate_signal(...)` below, and use
    `result.qualifies` / `result.composite_score` instead of the raw
    count.
  - Call `get_market_regime()` once per scan (not once per stock) and
    pass it into every `evaluate_signal()` call — it's slow to refetch
    per symbol.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
import yfinance as yf


# ---------------------------------------------------------------------
# 1. MARKET REGIME FILTER
# ---------------------------------------------------------------------

@dataclass
class MarketRegime:
    is_bullish: bool
    index_price: float
    index_ema50: float
    note: str


def get_market_regime(index_symbol: str = "^NSEI") -> MarketRegime:
    """
    Checks whether the broad market (default: NIFTY 50) is itself in an
    uptrend. Used to flag/penalize signals that are really just beta to
    the index rather than stock-specific strength.

    Call this ONCE per scan cycle, not per stock.
    """
    try:
        data = yf.download(
            tickers=[index_symbol],
            period="6mo",
            interval="1d",
            auto_adjust=False,
            prepost=False,
            group_by="ticker",
            threads=False,
            progress=False,
            timeout=12,
        )
        if data is None or data.empty:
            return MarketRegime(True, 0, 0, "Index data unavailable — regime filter skipped")
        if isinstance(data.columns, __import__("pandas").MultiIndex):
            try:
                data = data[index_symbol].copy()
            except Exception:
                data = data.xs(index_symbol, axis=1, level=1).copy()
        if data.empty or len(data) < 55:
            return MarketRegime(True, 0, 0, "Index data unavailable — regime filter skipped")

        close = data["Close"]
        ema50 = close.ewm(span=50, adjust=False).mean()
        last_price = float(close.iloc[-1])
        last_ema50 = float(ema50.iloc[-1])

        return MarketRegime(
            is_bullish=last_price > last_ema50,
            index_price=last_price,
            index_ema50=last_ema50,
            note="NIFTY above 50-EMA" if last_price > last_ema50 else "NIFTY below 50-EMA",
        )
    except Exception as e:
        return MarketRegime(True, 0, 0, f"Regime check failed ({e}) — filter skipped")


# ---------------------------------------------------------------------
# 2. CATEGORY-BASED SIGNAL EVALUATION
# ---------------------------------------------------------------------

@dataclass
class SignalBreakdown:
    trend_votes: int          # 0-3 (EMA structure, Supertrend, MACD)
    trend_agreement: float    # 0.0-1.0
    momentum_ok: bool         # RSI > 50
    participation_votes: int  # 0-2 (VWAP, Relative Volume)
    adx_gate_passed: bool     # ADX >= threshold (trend exists at all)
    regime_aligned: bool      # stock direction matches index direction
    qualifies: bool
    composite_score: int      # 0-100, for ranking
    reasons: Dict[str, bool] = field(default_factory=dict)


def evaluate_signal(
    *,
    price: float,
    ema20: float,
    ema50: float,
    vwap: float,
    rsi14: float,
    adx14: float,
    relative_volume: float,
    supertrend_bullish: bool,
    macd: float,
    macd_signal: float,
    regime: MarketRegime,
    adx_threshold: float = 20.0,
    relvol_threshold: float = 1.20,
    min_trend_agreement: float = 2 / 3,   # at least 2 of 3 trend indicators must agree
    require_momentum: bool = True,
    min_participation_votes: int = 1,     # at least 1 of {VWAP, RelVol}
    require_regime_alignment: bool = False,  # set True to hard-block counter-index trades
) -> SignalBreakdown:
    """
    Category-based bullish confirmation. Replaces flat "X of 7" counting.
    """

    # --- Trend category (collapsed, not counted as 3 separate signals) ---
    ema_bullish = price > ema20 > ema50
    trend_flags = [ema_bullish, supertrend_bullish, macd > macd_signal]
    trend_votes = sum(trend_flags)
    trend_agreement = trend_votes / 3

    # --- Momentum category ---
    momentum_ok = rsi14 > 50

    # --- Participation category (intraday direction + real volume behind it) ---
    participation_flags = [price > vwap, relative_volume >= relvol_threshold]
    participation_votes = sum(participation_flags)

    # --- Trend-strength gate (not a vote — a precondition) ---
    adx_gate_passed = adx14 >= adx_threshold

    # --- Regime check ---
    regime_aligned = regime.is_bullish  # stock is bullish AND index is bullish

    # --- Qualification logic ---
    qualifies = (
        trend_agreement >= min_trend_agreement
        and (momentum_ok if require_momentum else True)
        and participation_votes >= min_participation_votes
        and adx_gate_passed
        and (regime_aligned if require_regime_alignment else True)
    )

    # --- Composite score (0-100) for ranking within the qualified list ---
    # Weights: trend 40, momentum 20, participation 20, ADX strength 10, regime 10
    score = 0.0
    score += trend_agreement * 40
    score += (20 if momentum_ok else 0)
    score += (participation_votes / 2) * 20
    score += min(adx14 / 40, 1.0) * 10          # scales up to ADX=40, then caps
    score += (10 if regime_aligned else 0)

    return SignalBreakdown(
        trend_votes=trend_votes,
        trend_agreement=round(trend_agreement, 2),
        momentum_ok=momentum_ok,
        participation_votes=participation_votes,
        adx_gate_passed=adx_gate_passed,
        regime_aligned=regime_aligned,
        qualifies=qualifies,
        composite_score=round(score),
        reasons={
            "EMA structure bullish": ema_bullish,
            "Supertrend bullish": supertrend_bullish,
            "MACD > Signal": macd > macd_signal,
            "RSI > 50": momentum_ok,
            "Price > VWAP": price > vwap,
            f"RelVol >= {relvol_threshold}x": relative_volume >= relvol_threshold,
            f"ADX >= {adx_threshold} (trend exists)": adx_gate_passed,
            "Index (NIFTY) also bullish": regime_aligned,
        },
    )


# ---------------------------------------------------------------------
# 3. LIQUIDITY / TRADABILITY FILTER
# ---------------------------------------------------------------------

def passes_liquidity_filter(
    avg_daily_volume: float,
    avg_daily_value_inr: Optional[float] = None,
    min_volume: float = 200_000,
    min_value_inr: float = 50_000_000,  # 5 crore/day, adjust to taste
) -> bool:
    """
    Filters out illiquid stocks where a 1.2x relative-volume spike can
    happen on a handful of trades, and where the calculated entry/exit
    price in Trade Setup may not actually be fillable.
    """
    if avg_daily_volume < min_volume:
        return False
    if avg_daily_value_inr is not None and avg_daily_value_inr < min_value_inr:
        return False
    return True


# ---------------------------------------------------------------------
# 4. EXAMPLE USAGE (remove once integrated into app.py)
# ---------------------------------------------------------------------

if __name__ == "__main__":
    regime = get_market_regime()
    print(f"Market regime: {regime.note}")

    example = evaluate_signal(
        price=2545.0, ema20=2510.0, ema50=2480.0, vwap=2530.0,
        rsi14=61.0, adx14=27.0, relative_volume=1.8,
        supertrend_bullish=True, macd=12.4, macd_signal=9.1,
        regime=regime,
    )
    print(f"Qualifies: {example.qualifies}  Score: {example.composite_score}/100")
    for reason, passed in example.reasons.items():
        print(f"  {'✓' if passed else '✗'} {reason}")