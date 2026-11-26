"""Shared configuration and path resolution for local and Databricks Serverless."""

from __future__ import annotations

import os
from pathlib import Path

ORIGIN_DATE = "2023-07-01"
EVAL_START = "2023-08-05"
EVAL_END = "2026-09-30"
N_DAYS = 1188

CANALES = ("sala", "domicilio", "autoking")
TIPOS = ("caida", "subida")
SPECIAL_MMDD = ("12-24", "12-25", "12-31", "01-01", "01-05", "01-06")

RAMPUP_DAYS = 56
RAMPDOWN_DAYS = 28
REFORMA_MIN_GAP_DAYS = 7
REFORMA_MARGIN_DAYS = 7

MIN_EXPECTED_VENTAS = 150.0
MIN_EXPECTED_TICKETS = 25.0

SYSTEMIC_SHARE_THRESHOLD = 0.35
SYSTEMIC_Z_THRESHOLD = 2.0

DATABRICKS_SHARED_DIR = Path(
    "/Workspace/Shared/Dataset-rbe/Departamento de IA y Analitica Avanzada - Hackathon INDESIA/1_ESTUDIANTES"
)
DATABRICKS_UPLOAD_DIR = Path("/Volumes/dat_hack_rb06/raw_sch/raw_vol/uploads")


def get_repo_root() -> Path:
    """Return repository root containing databricks/."""
    return Path(__file__).resolve().parents[2]


def get_data_dir() -> Path:
    """Resolve the read-only input directory across local mirror and Databricks."""
    env_dir = os.environ.get("RBE_DATA_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend([
        DATABRICKS_SHARED_DIR,
        get_repo_root() / "databricks_data" / "1_ESTUDIANTES",
    ])
    for candidate in candidates:
        if (candidate / "ventas_diarias.csv").is_file():
            return candidate
    searched = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"Could not locate 1_ESTUDIANTES data directory. Checked: {searched}")


def get_outputs_dir(create: bool = True) -> Path:
    """Return git-ignored outputs directory for cached features and validation CSVs."""
    env_out = os.environ.get("RBE_OUTPUTS_DIR")
    out_dir = Path(env_out) if env_out else get_repo_root() / "outputs"
    if create:
        out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir
