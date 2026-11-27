"""Unit tests for Silver guardrails, direction rules, scoring constraints, and submission validator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from databricks.src.classifiers import HybridMetaClassifier, resolve_official_tipo
from databricks.src.config import get_repo_root
from databricks.src.guardrails import apply_guardrails
from databricks.src.scoring import build_gold_outputs, export_anomalias_csv
from databricks.src.silver import prepare_silver_data

BACKEND_VALID_REASONS = {
    "normal", "festivo", "vispera_festivo", "fecha_especial", "navidad", "semana_santa",
    "apertura", "cierre", "reforma", "canal_pequeno", "dia_neutro", "sin_historico", "fuera_periodo",
}


def _load_jury_validator():
    val_path = get_repo_root() / "databricks_data" / "1_ESTUDIANTES" / "validar_entrega.py"
    spec = importlib.util.spec_from_file_location("validar_entrega", str(val_path))
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _make_mini_datasets():
    dates = pd.date_range("2023-07-01", "2024-02-01", freq="D")
    # Minimal 2-province calendar covering national Dec 25 / Jan 6 and normal Fridays
    cal_rows = []
    for prov, ccaa in [("madrid", "comunidad-de-madrid"), ("sevilla", "andalucia")]:
        for d in dates:
            dow = d.day_of_week + 1
            md = d.strftime("%m-%d")
            tipo_fest = "national_holidays" if md in ("12-25", "01-01", "01-06") else ""
            visp = 1 if md in ("12-24", "12-31", "01-05") else 0
            fesp = {"12-24": "noche_buena", "12-25": "navidad", "12-31": "noche_vieja", "01-01": "new_year"}.get(md, "")
            tipo_dia = "vispera" if dow == 5 and not tipo_fest else ("festivo" if dow in (6, 7) or tipo_fest else "laborable")
            cal_rows.append({
                "fecha": d,
                "day_idx": (d - pd.Timestamp("2023-07-01")).days,
                "comunidad_autonoma": ccaa,
                "provincia": prov,
                "dia_semana": dow,
                "tipo_festivo": tipo_fest,
                "laborable": 0 if tipo_fest or dow in (6, 7) else 1,
                "vispera_festivo": visp,
                "post_festivo": 0,
                "puente": 0,
                "fin_de_semana_largo": "",
                "navidad": 1 if md in ("12-24", "12-25", "12-31", "01-01", "01-05", "01-06") else 0,
                "semana_santa": 0,
                "fecha_especial": fesp,
                "tipo_dia": tipo_dia,
                "periodo_especial": 0,
            })
    cal = pd.DataFrame(cal_rows)

    # Replicate calendar for 48 synthetic provinces so >=40 threshold triggers cleanly
    cal_list = [cal]
    for k in range(2, 48):
        c2 = cal[cal["provincia"] == "sevilla"].copy()
        c2["provincia"] = f"prov_{k}"
        c2["comunidad_autonoma"] = f"ccaa_{k % 10}"
        cal_list.append(c2)
    cal_full = pd.concat(cal_list, ignore_index=True)

    restaurantes = pd.DataFrame([
        {
            "restaurante_id": "R001", "marca": "Burger King", "pais": "España",
            "comunidad_autonoma": "comunidad-de-madrid", "provincia": "madrid",
            "tipologia": "fs", "tamano": "g", "ubicacion": "ordinal", "imagen": "prime",
            "terraza": "si", "autoking": "no", "distancia_reparto": "-",
            "estado": "estandar", "primera_venta": "2023-07-01", "ultima_venta": "2024-02-01",
        },
        {
            "restaurante_id": "R158", "marca": "Burger King", "pais": "España",
            "comunidad_autonoma": "", "provincia": "",
            "tipologia": "", "tamano": "", "ubicacion": "", "imagen": "",
            "terraza": "", "autoking": "", "distancia_reparto": "",
            "estado": "", "primera_venta": "2023-07-01", "ultima_venta": "2024-02-01",
        },
    ])

    v_rows = []
    for rid in ("R001", "R158"):
        for d in dates:
            # Create an 8-day reforma gap for R001 from 2023-10-10 to 2023-10-17
            if rid == "R001" and pd.Timestamp("2023-10-10") <= d <= pd.Timestamp("2023-10-17"):
                continue
            v_rows.append({
                "restaurante_id": rid,
                "fecha": d,
                "day_idx": (d - pd.Timestamp("2023-07-01")).days,
                "canal": "sala",
                "ventas": 2000.0,
                "tickets": 160,
                "ticket_medio": 12.50,
            })
    ventas = pd.DataFrame(v_rows)
    return ventas, restaurantes, cal_full


def test_orphan_national_fallback_and_friday_not_excluded():
    ventas, restaurantes, cal = _make_mini_datasets()
    silver = prepare_silver_data(ventas, restaurantes, cal)
    df = silver["df"]
    dim = silver["restaurants_dim"]

    assert bool(dim.loc[dim["restaurante_id"] == "R158", "is_orphan_master"].iloc[0]) is True
    assert bool(dim.loc[dim["restaurante_id"] == "R001", "is_orphan_master"].iloc[0]) is False

    # Orphan gets Jan 6 (Reyes) & Dec 25 excluded via national fallback even with fecha_especial == ""
    r158_jan6 = df[(df["restaurante_id"] == "R158") & (df["fecha"] == pd.Timestamp("2024-01-06"))]
    assert bool(r158_jan6["in_holiday_excl"].iloc[0]) is True

    # Normal Friday (2023-09-15, tipo_dia='vispera', vispera_festivo=0) must NOT be excluded
    fri = df[(df["restaurante_id"] == "R001") & (df["fecha"] == pd.Timestamp("2023-09-15"))]
    assert fri["tipo_dia"].iloc[0] == "vispera"
    assert bool(fri["in_holiday_excl"].iloc[0]) is False

    # Reforma +/-7d around 2023-10-10..17 gap excludes 2023-10-09 and 2023-10-18
    assert bool(df[(df["restaurante_id"] == "R001") & (df["fecha"] == pd.Timestamp("2023-10-09"))]["in_reforma_excl"].iloc[0]) is True
    assert bool(df[(df["restaurante_id"] == "R001") & (df["fecha"] == pd.Timestamp("2023-10-18"))]["in_reforma_excl"].iloc[0]) is True
    assert bool(df[(df["restaurante_id"] == "R001") & (df["fecha"] == pd.Timestamp("2023-10-26"))]["in_reforma_excl"].iloc[0]) is False


def test_guardrails_check_expected_not_observed_values():
    df = pd.DataFrame([
        # Severe collapse: observed 10 EUR / 1 ticket, but expected 1000 EUR / 80 tickets -> MUST be eligible!
        {
            "restaurante_id": "R001", "canal": "sala", "fecha": pd.Timestamp("2023-09-15"),
            "ventas": 10.0, "tickets": 1, "ventas_esperadas": 1000.0, "tickets_esperados": 80.0,
            "in_warmup": False, "in_holiday_excl": False, "in_lifecycle_excl": False,
            "in_reforma_excl": False, "is_systemic_day": False,
        },
        # Small expected channel: expected 120 EUR / 10 tickets -> MUST be excluded!
        {
            "restaurante_id": "R001", "canal": "domicilio", "fecha": pd.Timestamp("2023-09-15"),
            "ventas": 200.0, "tickets": 30, "ventas_esperadas": 120.0, "tickets_esperados": 10.0,
            "in_warmup": False, "in_holiday_excl": False, "in_lifecycle_excl": False,
            "in_reforma_excl": False, "is_systemic_day": False,
        },
        # Systemic blackout day -> MUST be excluded as 'dia_neutro'
        {
            "restaurante_id": "R002", "canal": "sala", "fecha": pd.Timestamp("2025-04-28"),
            "ventas": 2000.0, "tickets": 150, "ventas_esperadas": 1000.0, "tickets_esperados": 80.0,
            "in_warmup": False, "in_holiday_excl": False, "in_lifecycle_excl": False,
            "in_reforma_excl": False, "is_systemic_day": True,
        },
    ])
    g = apply_guardrails(df)
    assert bool(g.loc[0, "is_eligible_for_alert"]) is True
    assert g.loc[0, "exclusion_reason"] is None
    assert bool(g.loc[1, "is_eligible_for_alert"]) is False
    assert g.loc[1, "exclusion_reason"] == "canal_pequeno"
    assert bool(g.loc[2, "is_eligible_for_alert"]) is False
    assert g.loc[2, "exclusion_reason"] == "dia_neutro"


def test_opposite_sign_direction_rule():
    df = pd.DataFrame({
        "z_ventas": [0.5, -1.1, -4.5, 4.2],
        "z_tickets": [-4.2, 5.0, 1.0, -0.5],
    })
    tipos = resolve_official_tipo(df)
    assert list(tipos) == ["caida", "subida", "caida", "subida"]


def test_gold_export_passes_jury_validator(tmp_path: Path):
    ventas, restaurantes, cal = _make_mini_datasets()
    silver = prepare_silver_data(ventas, restaurantes, cal)
    df = silver["df"].copy()

    df["ventas_esperadas"] = 2000.0
    df["ventas_p05"] = 1600.0
    df["ventas_p95"] = 2400.0
    df["tickets_esperados"] = 160.0
    df["tickets_p05"] = 125.0
    df["tickets_p95"] = 195.0
    df["ticket_medio_esperado"] = 12.5
    df["impacto_estimado_eur"] = -1200.0
    df["desviacion_ventas_pct"] = -60.0
    df["desviacion_tickets_pct"] = -55.0
    df["z_ventas"] = -5.1
    df["z_tickets"] = -4.6
    df["z_ticket_medio"] = -0.3
    df["z_max"] = 5.1
    df["z_peer_diff"] = -5.0
    df["peer_delta_pct"] = -58.0
    df["z_cross_channel"] = -4.8
    df["otros_canales_normales"] = True
    df["is_systemic_day"] = False
    df = apply_guardrails(df)

    # Simulate 2 consecutive alert days for R001
    df.loc[(df["restaurante_id"] == "R001") & (df["fecha"].isin([pd.Timestamp("2023-09-12"), pd.Timestamp("2023-09-13")])), "ventas"] = 800.0
    pred = pd.DataFrame([
        {"restaurante_id": "R001", "canal": "sala", "fecha": pd.Timestamp("2023-09-12"), "tipo": "caida", "score": 0.96},
        {"restaurante_id": "R001", "canal": "sala", "fecha": pd.Timestamp("2023-09-13"), "tipo": "caida", "score": 0.94},
    ])

    gold = build_gold_outputs(
        df_features=df,
        restaurants_dim=silver["restaurants_dim"],
        pred_alerts=pred,
        run_id="run_test",
        model_version="v_test",
        synthetic_metrics={"f1": 0.85, "precision": 0.88, "recall": 0.82, "precision_at_100": 0.99},
        params_dict={"threshold": 0.55},
    )
    alerts = gold["alerts_prioritized"]
    assert len(alerts) == 2
    assert list(alerts["episode_days"]) == [2, 2]
    assert alerts["episode_id"].nunique() == 1
    assert alerts["priority_score"].between(0, 100).all()
    assert alerts["score"].between(0.50, 0.999999).all()
    assert float(alerts.loc[0, "score"]) > float(alerts.loc[1, "score"])

    # Verify exclusion_reason stays strictly within backend REASONS vocabulary
    reasons_used = {r for r in gold["series_summary"]["exclusion_reason"].dropna().unique()}
    assert reasons_used.issubset(BACKEND_VALID_REASONS)
    assert "sin_historico" in reasons_used
    assert "reforma" in reasons_used
    assert "fecha_especial" in reasons_used or "festivo" in reasons_used

    # Verify clean Spanish geo labels (no raw slug like 'comunidad-de-madrid')
    assert not alerts["explicacion_negocio"].str.contains("comunidad-de-madrid", regex=False).any()

    csv_path = export_anomalias_csv(alerts, tmp_path / "anomalias.csv")
    csv_df = pd.read_csv(csv_path)
    assert "recomendacion" in csv_df.columns
    validator = _load_jury_validator()
    parsed, errores, _avisos = validator.validar(str(csv_path))
    assert errores == []
    assert len(parsed) == 2


def test_hybrid_meta_decoupled_threshold_and_pure_ticket():
    rng = np.random.default_rng(7)
    n = 600
    rests = np.where(np.arange(n) < 150, "R001", np.where(np.arange(n) < 300, "R002", np.where(np.arange(n) < 450, "R003", "R004")))
    df = pd.DataFrame({
        "restaurante_id": rests,
        "canal": np.where(np.arange(n) % 2 == 0, "sala", "domicilio"),
        "fecha": pd.date_range("2024-01-01", periods=n, freq="D"),
        "z_ventas": rng.normal(0.0, 0.7, size=n),
        "z_tickets": rng.normal(0.0, 0.7, size=n),
        "z_ticket_medio": rng.normal(0.0, 0.5, size=n),
        "z_peer_diff": rng.normal(0.0, 0.6, size=n),
        "z_peer_diff_t": rng.normal(0.0, 0.6, size=n),
        "z_cross_channel": rng.normal(0.0, 0.6, size=n),
        "otros_canales_normales": True,
        "delta_channel_share": rng.normal(0.0, 0.02, size=n),
        "cqr_exceed_v": rng.uniform(-0.8, 0.2, size=n),
        "cqr_exceed_t": rng.uniform(-0.8, 0.2, size=n),
        "z_neighbor_same_sign": np.zeros(n),
        "desviacion_ventas_pct": rng.normal(0.0, 6.0, size=n),
        "desviacion_tickets_pct": rng.normal(0.0, 6.0, size=n),
        "ventas_esperadas": np.full(n, 900.0),
        "tickets_esperados": np.full(n, 45.0),
        "impacto_estimado_eur": rng.normal(0.0, 50.0, size=n),
        "is_eligible_for_alert": np.ones(n, dtype=bool),
    })
    y = np.zeros(n, dtype=np.int32)
    # Inject pure_ticket surges (low EUR impact, high ticket deviation)
    pure_idx = np.array([20, 80, 170, 230, 320, 390, 470, 520])
    df.loc[pure_idx, "z_tickets"] = 5.5
    df.loc[pure_idx, "cqr_exceed_t"] = 3.8
    df.loc[pure_idx, "desviacion_tickets_pct"] = 140.0
    df.loc[pure_idx, "impacto_estimado_eur"] = 15.0  # low EUR impact
    y[pure_idx] = 2

    drop_idx = np.array([40, 100, 190, 260, 350, 420, 500, 550])
    df.loc[drop_idx, "z_ventas"] = -5.8
    df.loc[drop_idx, "z_tickets"] = -5.4
    df.loc[drop_idx, "cqr_exceed_v"] = 4.1
    df.loc[drop_idx, "impacto_estimado_eur"] = -700.0
    y[drop_idx] = 1

    df["z_max"] = np.maximum.reduce([np.abs(df["z_ventas"]), np.abs(df["z_tickets"]), np.abs(df["z_ticket_medio"])])
    eval_mask = np.ones(n, dtype=bool)

    clf = HybridMetaClassifier().fit(df, y_train=y, eval_mask_train=eval_mask)
    assert 0.0 < clf.threshold < 1.0
    pred = clf.predict_alerts(df)
    assert len(pred) >= 12
    assert set(pred["tipo"]).issubset({"caida", "subida"})


def test_consolidated_episodes_and_multichannel_incidents():
    from databricks.src.scoring import _assign_episodes, _assign_incidents

    # Simulate R057 pattern (gaps of 2d, 2d, 1d, 3d) + San Juan bridge + multi-channel incident (+-1d)
    alerts = pd.DataFrame([
        {"restaurante_id": "R057", "canal": "sala", "fecha": pd.Timestamp("2025-03-28").date(), "tipo": "caida", "impacto_estimado_eur": -10000.0},
        {"restaurante_id": "R057", "canal": "sala", "fecha": pd.Timestamp("2025-03-30").date(), "tipo": "caida", "impacto_estimado_eur": -8800.0},
        {"restaurante_id": "R057", "canal": "sala", "fecha": pd.Timestamp("2025-04-01").date(), "tipo": "caida", "impacto_estimado_eur": -9200.0},
        {"restaurante_id": "R057", "canal": "sala", "fecha": pd.Timestamp("2025-04-02").date(), "tipo": "caida", "impacto_estimado_eur": -8200.0},
        {"restaurante_id": "R057", "canal": "sala", "fecha": pd.Timestamp("2025-04-05").date(), "tipo": "caida", "impacto_estimado_eur": -8300.0},
        # Multi-channel within +-1d on R010
        {"restaurante_id": "R010", "canal": "sala", "fecha": pd.Timestamp("2025-05-10").date(), "tipo": "caida", "impacto_estimado_eur": -1200.0},
        {"restaurante_id": "R010", "canal": "domicilio", "fecha": pd.Timestamp("2025-05-11").date(), "tipo": "caida", "impacto_estimado_eur": -900.0},
    ])
    ep = _assign_episodes(alerts)
    r57 = ep[ep["restaurante_id"] == "R057"]
    assert r57["episode_id"].nunique() == 1
    assert r57["episode_id"].iloc[0] == "R057_sala_20250328"
    assert int(r57["episode_days"].iloc[0]) == 5
    assert str(r57["episode_start_date"].iloc[0]) == "2025-03-28"
    assert str(r57["episode_end_date"].iloc[0]) == "2025-04-05"

    inc = _assign_incidents(ep)
    r10 = inc[inc["restaurante_id"] == "R010"]
    assert r10["incident_id"].nunique() == 1
    assert bool(r10["incident_multicanal"].iloc[0]) is True
    assert r10["incident_canales"].iloc[0] == ["domicilio", "sala"]
