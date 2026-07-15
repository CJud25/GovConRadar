"""
charts.py — reusable Plotly chart builders. Every chart pulls colors from
theme.py so the whole app (and the Power BI report) share one visual language.
All builders guard against empty input and return a styled figure.
"""

import math

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from components import theme

_EMPTY_MSG = "No data for the current filters"


def _empty_fig(height=360):
    fig = go.Figure()
    fig.add_annotation(text=_EMPTY_MSG, showarrow=False, font=dict(color=theme.GRAY, size=14))
    fig.update_layout(xaxis_visible=False, yaxis_visible=False)
    return theme.style(fig, height=height)


def value_by_bucket(candidates: pd.DataFrame, height=360):
    if candidates.empty or "expiration_bucket" not in candidates:
        return _empty_fig(height)
    g = (candidates.groupby("expiration_bucket")["total_obligated_amount"].sum()
         .reindex(theme.BUCKET_ORDER).dropna().reset_index())
    g["value_fmt"] = g["total_obligated_amount"].map(theme.usd_short)
    # Per-bucket colors: the "Expired — verify" quarantine bar is gray and reads as
    # separate from the forward-looking windows — never blended into a pipeline total.
    colors = [theme.BUCKET_COLORS.get(b, theme.STEEL) for b in g["expiration_bucket"]]
    fig = px.bar(g, x="expiration_bucket", y="total_obligated_amount", custom_data=["value_fmt"],
                 labels={"expiration_bucket": "Expiration window", "total_obligated_amount": "Pipeline value"})
    fig.update_traces(marker_color=colors,
                      hovertemplate="<b>%{x}</b><br>%{customdata[0]}<extra></extra>")
    theme.money_axis(fig, "y")
    return theme.style(fig, height=height, title="Pipeline value by expiration window (expired shown separately)")


def top_bar(candidates: pd.DataFrame, group_col: str, title: str, n=10, height=360):
    if candidates.empty or group_col not in candidates:
        return _empty_fig(height)
    g = (candidates.groupby(group_col)["total_obligated_amount"].sum()
         .sort_values(ascending=True).tail(n).reset_index())
    g["value_fmt"] = g["total_obligated_amount"].map(theme.usd_short)
    fig = px.bar(g, x="total_obligated_amount", y=group_col, orientation="h", custom_data=["value_fmt"],
                 labels={group_col: "", "total_obligated_amount": "Value"})
    fig.update_traces(marker_color=theme.NAVY,
                      hovertemplate="<b>%{y}</b><br>%{customdata[0]}<extra></extra>")
    theme.money_axis(fig, "x")
    return theme.style(fig, height=height, title=title)


def score_vs_value(candidates: pd.DataFrame, height=420):
    if candidates.empty or "pursuit_score" not in candidates:
        return _empty_fig(height)
    df = candidates.copy()
    df["total_obligated_amount"] = df["total_obligated_amount"].fillna(0)
    df["value_fmt"] = df["total_obligated_amount"].map(theme.usd_short)
    if "title_display" not in df.columns:  # never surface the raw record in a hover
        df["title_display"] = df.get("candidate_id", "")
    fig = px.scatter(
        df, x="total_obligated_amount", y="pursuit_score",
        color="priority_tier", color_discrete_map=theme.TIER_COLORS,
        custom_data=["title_display", "subagency", "incumbent_vendor", "value_fmt"],
        labels={"total_obligated_amount": "Estimated value", "pursuit_score": "Pursuit score",
                "priority_tier": "Priority tier"},
    )
    # Fixed marker size — value is already the x-axis; don't double-encode it.
    fig.update_traces(marker=dict(size=9, opacity=0.75, line=dict(width=0.5, color="white")),
                      hovertemplate=("<b>%{customdata[0]}</b><br>%{customdata[1]} · %{customdata[2]}"
                                     "<br>Value %{customdata[3]} · Score %{y:.0f}<extra></extra>"))
    theme.money_axis(fig, "x")
    return theme.style(fig, height=height, title="Pursuit score vs. estimated value")


def capture_phase_bar(candidates: pd.DataFrame, height=360):
    if candidates.empty or "capture_phase" not in candidates:
        return _empty_fig(height)
    order = [p for p in theme.PHASE_COLORS if p in candidates["capture_phase"].unique()]
    g = candidates["capture_phase"].value_counts().reindex(order).dropna().reset_index()
    g.columns = ["capture_phase", "count"]
    # Single color — the x-axis already names the phase; rainbow bars double-encode.
    fig = px.bar(g, x="capture_phase", y="count", labels={"capture_phase": "", "count": "Candidates"})
    fig.update_traces(marker_color=theme.NAVY,
                      hovertemplate="<b>%{x}</b><br>%{y} candidates<extra></extra>")
    fig.update_layout(showlegend=False)
    return theme.style(fig, height=height, title="Candidates by capture phase")


def ptw_strip(comps: pd.DataFrame, p25, p50, p75, incumbent_runrate=None, height=240):
    """Competitive Price Range as a strip plot: one dot per comparable award's
    annual run-rate, a shaded P25–P75 band, the market-median line, and the
    incumbent's run-rate as a separate marker. Showing the dots (not just the band)
    is the honest uncertainty picture — a tight cluster and a shotgun blast produce
    identical bars but very different trust. In-progress awards are colored apart
    because their obligated-to-date understates their eventual value."""
    if comps is None or comps.empty:
        return _empty_fig(height)
    df = comps.copy()
    df["comp_run_rate"] = pd.to_numeric(df["comp_run_rate"], errors="coerce")
    df = df.dropna(subset=["comp_run_rate"])
    if df.empty:
        return _empty_fig(height)
    in_prog = df["comp_in_progress"].fillna(False).astype(bool) if "comp_in_progress" in df else pd.Series(False, index=df.index)
    # deterministic vertical jitter so overlapping dots separate without randomness
    y = [((i % 7) - 3) * 0.05 for i in range(len(df))]

    fig = go.Figure()
    fig.add_shape(type="rect", x0=p25, x1=p75, y0=-0.5, y1=0.5, line_width=0,
                  fillcolor="rgba(62,124,177,0.14)", layer="below")  # P25–P75 band
    fig.add_shape(type="line", x0=p50, x1=p50, y0=-0.5, y1=0.5,
                  line=dict(color=theme.NAVY, width=2))               # market median
    for flag, color, name in [(False, theme.STEEL, "Completed award"), (True, theme.AMBER, "In-progress (obligated-to-date)")]:
        m = in_prog.to_numpy() == flag
        if not m.any():
            continue
        sub = df[m]
        fig.add_trace(go.Scatter(
            x=sub["comp_run_rate"], y=[y[i] for i in range(len(df)) if m[i]],
            mode="markers", name=name,
            marker=dict(size=10, color=color, opacity=0.8, line=dict(width=0.5, color="white")),
            customdata=sub[["comp_piid", "comp_agency"]].to_numpy() if {"comp_piid", "comp_agency"}.issubset(sub.columns) else None,
            hovertemplate="%{customdata[0]} · %{customdata[1]}<br>run-rate %{x:$,.0f}/yr<extra></extra>"
                          if {"comp_piid", "comp_agency"}.issubset(sub.columns) else "run-rate %{x:$,.0f}/yr<extra></extra>",
        ))
    if incumbent_runrate:
        fig.add_trace(go.Scatter(
            x=[incumbent_runrate], y=[0], mode="markers", name="Incumbent run-rate",
            marker=dict(size=15, color=theme.REDORANGE, symbol="diamond", line=dict(width=1, color="white")),
            hovertemplate="Incumbent run-rate %{x:$,.0f}/yr<extra></extra>"))
    fig.update_yaxes(visible=False, range=[-0.6, 0.6])
    theme.money_axis(fig, "x")
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), showlegend=True)
    return theme.style(fig, height=height, title="Comparable award run-rates (annualized)")


def recompete_timeline(candidates: pd.DataFrame, height=460):
    cols = {"estimated_recompete_window_start", "estimated_recompete_window_end"}
    if candidates.empty or not cols.issubset(candidates.columns):
        return _empty_fig(height)
    df = candidates.dropna(subset=list(cols)).copy()
    if df.empty:
        return _empty_fig(height)
    label_src = df["title_display"] if "title_display" in df.columns else df.get("contract_title", df["candidate_id"])
    df["label"] = label_src.astype(str).str.slice(0, 40)
    if "total_obligated_amount" in df:
        df["value_fmt"] = df["total_obligated_amount"].map(theme.usd_short)
    fig = px.timeline(
        df, x_start="estimated_recompete_window_start", x_end="estimated_recompete_window_end",
        y="label", color="priority_tier", color_discrete_map=theme.TIER_COLORS,
        hover_data={c: True for c in ["subagency", "incumbent_vendor", "selected_expiration_date", "value_fmt"]
                    if c in df},
        labels={"label": "", "priority_tier": "Priority tier", "value_fmt": "Est. value"},
    )
    fig.update_yaxes(autorange="reversed")
    # "Today" marker so the runway reads as time-to-now.
    try:
        fig.add_vline(x=pd.Timestamp.now(), line_dash="dot", line_color=theme.CHARCOAL, opacity=0.45)
    except Exception:
        pass
    return theme.style(fig, height=height, title="Estimated recompete windows (ESTIMATE) — T-minus runway")


def expiration_histogram(candidates: pd.DataFrame, height=340):
    if candidates.empty or "selected_expiration_date" not in candidates:
        return _empty_fig(height)
    df = candidates.copy()
    df["selected_expiration_date"] = pd.to_datetime(df["selected_expiration_date"], errors="coerce")
    df = df.dropna(subset=["selected_expiration_date"])
    if df.empty:
        return _empty_fig(height)
    fig = px.histogram(df, x="selected_expiration_date", nbins=24, labels={"selected_expiration_date": "Expiration month"})
    fig.update_traces(marker_color=theme.STEEL)
    return theme.style(fig, height=height, title="Contract expirations over time")


def state_choropleth(candidates: pd.DataFrame, height=420):
    if candidates.empty or "place_of_performance_state" not in candidates:
        return _empty_fig(height)
    g = candidates.groupby("place_of_performance_state")["total_obligated_amount"].sum().reset_index()
    g = g[g["place_of_performance_state"].astype(str).str.len() == 2]
    if g.empty:
        return _empty_fig(height)
    fig = px.choropleth(g, locations="place_of_performance_state", locationmode="USA-states",
                        color="total_obligated_amount", scope="usa", color_continuous_scale="Blues",
                        labels={"total_obligated_amount": "Value ($)"})
    return theme.style(fig, height=height, title="Expiring value by place of performance")


def agency_psc_heatmap(candidates: pd.DataFrame, height=420, top_psc=15):
    # `agency` is a constant (all DoD) — the meaningful axis is the DoD component.
    if candidates.empty or not {"subagency", "psc"}.issubset(candidates.columns):
        return _empty_fig(height)
    pivot = candidates.pivot_table(index="subagency", columns="psc", values="total_obligated_amount",
                                   aggfunc="sum", fill_value=0)
    if pivot.empty:
        return _empty_fig(height)
    # Cap to the top-N PSCs by total value — the raw grid is ~220 PSC columns, an
    # unreadable wall. Keep the columns that carry the money; drop the long tail.
    top = pivot.sum(axis=0).sort_values(ascending=False).head(top_psc).index
    pivot = pivot[top]
    fig = px.imshow(pivot, color_continuous_scale="Blues", aspect="auto",
                    labels=dict(color="Value ($)", x="PSC", y="DoD Component"))
    return theme.style(fig, height=height, title=f"DoD Component × PSC concentration (top {top_psc} PSCs)")


def incumbent_pareto(candidates: pd.DataFrame, height=420):
    """Single-axis concentration: top-15 incumbents by pipeline value, each bar
    end direct-labeled with the RUNNING cumulative share — the Pareto insight
    without the dual-axis lie. Title states how many incumbents make 80%."""
    if candidates.empty or "incumbent_vendor" not in candidates:
        return _empty_fig(height)
    g = candidates.groupby("incumbent_vendor")["total_obligated_amount"].sum().sort_values(ascending=False)
    g = g[g > 0]
    if g.empty:
        return _empty_fig(height)
    cum_pct = g.cumsum() / g.sum() * 100
    n_for_80 = int((cum_pct < 80).sum()) + 1  # incumbents needed to reach 80%
    top = g.head(15)
    order = list(top.index[::-1])  # largest at top of a horizontal bar
    fig = go.Figure()
    fig.add_bar(
        y=order, x=[top[v] for v in order], orientation="h", marker_color=theme.STEEL,
        text=[f"{cum_pct[v]:.0f}%" for v in order], textposition="outside",
        textfont=dict(color=theme.REDORANGE, size=11, family=theme.FONT_MONO),
        customdata=[[theme.usd_short(top[v]), f"{cum_pct[v]:.0f}"] for v in order],
        hovertemplate="<b>%{y}</b><br>%{customdata[0]} · cumulative %{customdata[1]}%<extra></extra>",
    )
    theme.money_axis(fig, "x")
    fig.update_xaxes(title="Pipeline value")
    return theme.style(
        fig, height=height,
        title=f"Incumbent concentration — {n_for_80} incumbents = 80% of pipeline (labels: cumulative %)",
    )


def burn_pressure_bar(ceiling_ratio: float, time_ratio: float, band: str, height: int = 150):
    """Obligation-pace diverging bar for a MEASURED row: fraction of ceiling obligated minus
    fraction of PoP elapsed, centered on zero (on pace). Derives the signed value from its two
    FACT inputs; guards non-finite. Neutral color (Corrections C1.1) — direction is carried by
    the sign, the glyph, and the axis anchors, never by alarm color; ±0.20 prior thresholds are
    dotted guides. Called only for measured rows (guarded in detail.py)."""
    if not (math.isfinite(ceiling_ratio) and math.isfinite(time_ratio)):
        return _empty_fig(height)
    bp = round(ceiling_ratio - time_ratio, 4)
    x = max(-1.0, min(1.0, bp))
    fig = go.Figure()
    fig.add_bar(x=[x], y=["pace"], orientation="h", marker_color=theme.BURN_COLORS.get(band, theme.GRAY),
                hovertemplate=f"obligation pace {bp:+.2f}<extra></extra>")
    fig.add_shape(type="line", x0=0, x1=0, y0=-0.5, y1=0.5, line=dict(color=theme.CHARCOAL, width=2))  # on pace
    for gx in (-0.20, 0.20):
        fig.add_shape(type="line", x0=gx, x1=gx, y0=-0.5, y1=0.5,
                      line=dict(color=theme.GRID, width=1, dash="dot"))
    fig.add_annotation(x=-1.0, y=0, text="◀ behind pace", showarrow=False, xanchor="left",
                       font=dict(color=theme.GRAY, size=11))
    fig.add_annotation(x=1.0, y=0, text="ahead of pace ▶", showarrow=False, xanchor="right",
                       font=dict(color=theme.GRAY, size=11))
    fig.update_xaxes(range=[-1.05, 1.05], zeroline=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(showlegend=False)
    return theme.style(fig, height=height)


def hhi_concentration_bar(markets, height=360):
    """Incumbent concentration (descriptive): one horizontal bar per assessable DoD component,
    x = top-incumbent share of expiring obligated dollars (%), sorted descending. Single neutral
    accent (STEEL) — NO Herfindahl index, NO DOJ/FTC bands, NO band zones, NO green/amber/red;
    the share IS the read (Corrections v2, Option A). Each bar carries its incumbent count and
    $-coverage in the hover; Unknown (thin / low-coverage) markets live in the caption, not here.
    Empty input -> _empty_fig. Grep removal token: hhi_concentration."""
    rows = [m for m in markets if getattr(m, "assessable", False) and m.top_share is not None]
    if not rows:
        return _empty_fig(height)
    rows = sorted(rows, key=lambda m: m.top_share)  # ascending -> largest lands at top of a horizontal bar
    y = [m.market for m in rows]
    x = [m.top_share * 100 for m in rows]
    fig = go.Figure()
    fig.add_bar(
        y=y, x=x, orientation="h", marker_color=theme.STEEL,
        text=[f"{v:.0f}%" for v in x], textposition="outside",
        textfont=dict(color=theme.CHARCOAL, size=11, family=theme.FONT_MONO),
        customdata=[[m.n_ueis, f"{m.coverage * 100:.0f}"] for m in rows],
        hovertemplate=("<b>%{y}</b><br>top incumbent %{x:.0f}% of expiring $"
                       "<br>%{customdata[0]} incumbents · %{customdata[1]}% of $ attributed<extra></extra>"),
    )
    fig.update_xaxes(title="Top-incumbent share of expiring obligated $ (%)", range=[0, 100])
    return theme.style(fig, height=height,
                       title="Incumbent concentration — top-incumbent dollar-share by DoD component")


def scoring_breakdown_bar(breakdown_rows: pd.DataFrame, height=380):
    if breakdown_rows.empty:
        return _empty_fig(height)
    _DRIVEN = "Driven by your profile"
    _INTRINSIC = "Intrinsic to the contract"
    df_sorted = breakdown_rows.sort_values("weighted_score").copy()
    # Two-tone by provenance: profile-driven components (move when you edit your
    # profile) vs. facts intrinsic to the contract. Guard when the flag is absent.
    if "profile_driven" in df_sorted.columns:
        df_sorted["driver"] = df_sorted["profile_driven"].map(lambda v: _DRIVEN if v else _INTRINSIC)
    else:
        df_sorted["driver"] = _INTRINSIC
    fig = px.bar(
        df_sorted, x="weighted_score", y="score_component", orientation="h", color="driver",
        color_discrete_map={_DRIVEN: theme.STEEL, _INTRINSIC: theme.GRAY},
        labels={"weighted_score": "Weighted points", "score_component": "", "driver": ""},
    )
    # Preserve the ascending sort against Plotly's per-color regrouping.
    fig.update_yaxes(categoryorder="array", categoryarray=df_sorted["score_component"].tolist())
    fig.update_layout(legend=dict(orientation="h", y=-0.28, x=0, title_text=""))
    return theme.style(fig, height=height, title="Pursuit score breakdown (weighted contribution)")
