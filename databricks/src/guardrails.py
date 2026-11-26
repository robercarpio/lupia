"""Deterministic jury guardrails (Layer 3) preventing known false positives."""

from __future__ import annotations

import numpy as np
import pandas as pd

from databricks.src.config import MIN_EXPECTED_TICKETS, MIN_EXPECTED_VENTAS


def apply_guardrails(df: pd.DataFrame) -> pd.DataFrame:
    """Apply strict eligibility mask and granular UI-aligned exclusion_reason."""
    out = df.copy()
    n = len(out)

    warmup = out["in_warmup"].to_numpy(dtype=bool)
    holiday = out["in_holiday_excl"].to_numpy(dtype=bool)
    lifecycle = out["in_lifecycle_excl"].to_numpy(dtype=bool)
    reforma = out["in_reforma_excl"].to_numpy(dtype=bool)
    small_channel = (
        (out["ventas_esperadas"].to_numpy(dtype=np.float64) < MIN_EXPECTED_VENTAS)
        | (out["tickets_esperados"].to_numpy(dtype=np.float64) < MIN_EXPECTED_TICKETS)
    )
    systemic = out["is_systemic_day"].to_numpy(dtype=bool)

    # Granular breakdown matching backend/app/services/dataset.py (REASONS) & DetectorPanel.jsx
    has_special = (
        (out["fecha_especial"].astype(str).to_numpy() != "")
        if "fecha_especial" in out.columns
        else np.zeros(n, dtype=bool)
    ) | (
        out["in_special_mmdd"].to_numpy(dtype=bool)
        if "in_special_mmdd" in out.columns
        else np.zeros(n, dtype=bool)
    )
    has_festivo = (
        (out["tipo_festivo"].astype(str).to_numpy() != "")
        if "tipo_festivo" in out.columns
        else np.zeros(n, dtype=bool)
    )
    has_vispera = (
        out["vispera_festivo"].to_numpy(dtype=bool)
        if "vispera_festivo" in out.columns
        else np.zeros(n, dtype=bool)
    )
    rampup = (
        out["in_rampup"].to_numpy(dtype=bool)
        if "in_rampup" in out.columns
        else lifecycle
    )
    rampdown = (
        out["in_rampdown"].to_numpy(dtype=bool)
        if "in_rampdown" in out.columns
        else np.zeros(n, dtype=bool)
    )

    conditions = [
        warmup,
        holiday & has_special,
        holiday & has_festivo,
        holiday & has_vispera,
        holiday,
        lifecycle & rampup,
        lifecycle & rampdown,
        lifecycle,
        reforma,
        small_channel,
        systemic,
    ]
    choices = [
        "sin_historico",
        "fecha_especial",
        "festivo",
        "vispera_festivo",
        "festivo",
        "apertura",
        "cierre",
        "apertura",
        "reforma",
        "canal_pequeno",
        "dia_neutro",
    ]

    reason = np.select(conditions, choices, default="")
    eligible = ~(warmup | holiday | lifecycle | reforma | small_channel | systemic)

    out["exclusion_reason"] = pd.Series(
        np.where(eligible, None, reason), index=out.index, dtype=object
    )
    out["is_eligible_for_alert"] = eligible
    return out


def guardrail_funnel_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Summarize individual and cumulative exclusion counts across the dataset."""
    total = len(df)
    small_channel = (
        (df["ventas_esperadas"] < MIN_EXPECTED_VENTAS)
        | (df["tickets_esperados"] < MIN_EXPECTED_TICKETS)
    )
    flags = [
        ("1. Sin histórico (< 2023-08-05)", df["in_warmup"]),
        ("2. Festivos / víspera_festivo / especiales", df["in_holiday_excl"]),
        ("3. Ciclo de vida (56d apertura / 28d cierre)", df["in_lifecycle_excl"]),
        ("4. Reformas (+-7d en cierres >=7d)", df["in_reforma_excl"]),
        ("5. Canal pequeño esperado (<150 EUR o <25 tk)", small_channel),
        ("6. Día neutro sistémico (>=35% CCAA/Nac)", df["is_systemic_day"]),
    ]
    rows = []
    for label, s in flags:
        cnt = int(s.sum())
        rows.append({
            "regla": label,
            "filas_afectadas": cnt,
            "pct_total": round(100.0 * cnt / total, 2),
        })
    elig = int(df["is_eligible_for_alert"].sum())
    rows.append({
        "regla": "TOTAL UNIVERSO CANDIDATO ELEGIBLE",
        "filas_afectadas": elig,
        "pct_total": round(100.0 * elig / total, 2),
    })
    return pd.DataFrame(rows)
