"""Carga y validación de configuración desde .env.

Toda la parametrización del sistema vive acá: perfil del candidato,
pesos del scoring, queries, fuentes habilitadas, alertas, IA y Telegram.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT := PROJECT_ROOT)


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "")
    return val.strip() if val.strip() else default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    return _env(key, str(default)).strip().lower() in ("true", "1", "yes", "si")


def _env_list(key: str, default: str = "") -> list[str]:
    return [s.strip() for s in _env(key, default).split(",") if s.strip()]


def _env_pairs(key: str) -> dict[str, int]:
    """'tech lead:14,lead:10' → {'tech lead': 14, 'lead': 10}"""
    out = {}
    for pair in _env_list(key):
        if ":" in pair:
            k, v = pair.rsplit(":", 1)
            try:
                out[k.strip().lower()] = int(v)
            except ValueError:
                pass
    return out


@dataclass
class Profile:
    title: str
    years_exp: int
    techs: list[str]                      # orden = prioridad
    roles: list[str]
    english_level: str
    modality_pref: list[str]
    modality_bonus: dict[str, int]
    salary_min: int
    salary_max: int
    salary_flex: bool
    locations_accepted: list[str]
    locations_rejected: list[str]
    green_keywords: list[str]
    red_keywords: list[str]
    staffing_penalty: int


@dataclass
class Scoring:
    base: int
    n_tech_primary: int
    tech_primary: int
    tech_secondary: int
    role_keywords: dict[str, int]
    exp_match_bonus: int
    exp_mismatch_soft: int
    exp_mismatch_hard: int
    exp_hard_threshold: int
    english_excluyente: int
    english_deseable: int
    english_not_required: int
    us_hours_penalty: int
    salary_in_range_bonus: int
    salary_out_soft: int
    stack_overlap_per: float
    stack_overlap_max: float
    cap: int
    modality_bonus: dict[str, int] = field(default_factory=dict)


@dataclass
class Search:
    mode: str
    queries_linkedin: list[str]
    queries_computrabajo: list[str]
    queries_indeed: list[str]
    queries_glassdoor: list[str]
    queries_laborum: list[str]
    sample_linkedin: list[str]
    sample_indeed: list[str]
    sample_computrabajo: list[str]
    sample_glassdoor: list[str]
    sample_rotation: float


@dataclass
class IaConfig:
    enabled: bool
    model: str
    base_url: str
    api_key: str
    batch_size: int
    timeout: int
    retries: int
    run_hours_utc: list[int]


@dataclass
class Alerts:
    min_score: int
    max_per_digest: int
    worth_it_score: int


@dataclass
class Daemon:
    interval_min: int = 240        # (legacy) minutos entre barridos — reemplazado por sweep_hours
    page_size: int = 5             # ofertas por página del digest paginado
    sweep_hours_utc: list[int] = field(default_factory=lambda: [0, 4, 8, 12, 16, 20])


@dataclass
class ReportCfg:
    enabled: bool = True
    ia_narrative: bool = True      # false = PDF solo con datos, sin llamadas IA
    max_salary_samples: int = 60
    out_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "reports")




@dataclass
class Telegram:
    enabled: bool
    bot_token: str
    chat_id: str
    digest_page_size: int
    allowed_chats: tuple[int, ...] = ()


@dataclass
class Config:
    profile: Profile
    scoring: Scoring
    search: Search
    sources: dict[str, bool]
    premium_hours: list[int]
    ia: IaConfig
    telegram: Telegram
    daemon: Daemon
    alerts: Alerts
    report: ReportCfg
    project_root: Path = PROJECT_ROOT
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ofertas.sqlite"

    @property
    def csv_path(self) -> Path:
        return self.data_dir / "ofertas_detalle.csv"


def load_config(env_file: Path | None = None) -> Config:
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv(PROJECT_ROOT / ".env")

    techs = [t.strip().lower() for t in _env_list("PROFILE_TECHS")]
    scoring = Scoring(
        base=_env_int("SCORE_BASE", 40),
        n_tech_primary=_env_int("N_TECH_PRIMARY", 4),
        tech_primary=_env_int("SCORE_TECH_PRIMARY", 15),
        tech_secondary=_env_int("SCORE_TECH_SECONDARY", 8),
        role_keywords=_env_pairs("SCORE_ROLE_KEYWORDS"),
        exp_match_bonus=_env_int("SCORE_EXP_MATCH_BONUS", 2),
        exp_mismatch_soft=_env_int("SCORE_EXP_MISMATCH_SOFT", -3),
        exp_mismatch_hard=_env_int("SCORE_EXP_MISMATCH_HARD", -8),
        exp_hard_threshold=_env_int("SCORE_EXP_HARD_THRESHOLD", 8),
        english_excluyente=_env_int("SCORE_ENGLISH_EXCLUYENTE", -6),
        english_deseable=_env_int("SCORE_ENGLISH_DESEABLE", 0),
        english_not_required=_env_int("SCORE_ENGLISH_NOT_REQUIRED", 2),
        us_hours_penalty=_env_int("SCORE_US_HOURS_PENALTY", -4),
        salary_in_range_bonus=_env_int("SCORE_SALARY_IN_RANGE_BONUS", 5),
        salary_out_soft=_env_int("SCORE_SALARY_OUT_SOFT", -2),
        stack_overlap_per=_env_float("SCORE_STACK_OVERLAP_PER", 1.5),
        stack_overlap_max=_env_float("SCORE_STACK_OVERLAP_MAX", 6),
        cap=_env_int("SCORE_CAP", 98),
        modality_bonus={
            "remoto": _env_int("PROFILE_MOD_REMOTO_BONUS", 3),
            "híbrido": _env_int("PROFILE_MOD_HIBRIDO_BONUS", 0),
            "presencial": _env_int("PROFILE_MODALIDAD_PRESENCIAL_PENALTY", -4),
        },
    )
    profile = Profile(
        title=_env("PROFILE_TITLE"),
        years_exp=_env_int("PROFILE_YEARS_EXP", 5),
        techs=techs,
        roles=[r.lower() for r in _env_list("PROFILE_ROLES")],
        english_level=_env("PROFILE_ENGLISH_LEVEL", "B2"),
        modality_pref=_env_list("PROFILE_MODALITY_PREFERENCE"),
        modality_bonus=scoring.modality_bonus,
        salary_min=_env_int("PROFILE_SALARY_MIN", 2_500_000),
        salary_max=_env_int("PROFILE_SALARY_MAX", 3_200_000),
        salary_flex=_env_bool("PROFILE_SALARY_FLEX", True),
        locations_accepted=[l.lower() for l in _env_list("PROFILE_LOCATIONS_ACCEPTED")],
        locations_rejected=[l.lower() for l in _env_list("PROFILE_LOCATIONS_REJECTED")],
        green_keywords=_env_list("PROFILE_GREEN_KEYWORDS"),
        red_keywords=[r.lower() for r in _env_list("PROFILE_RED_KEYWORDS")],
        staffing_penalty=_env_int("PROFILE_STAFFING_PENALTY", -5),
    )
    search = Search(
        mode=_env("SEARCH_MODE", "both"),
        queries_linkedin=_env_list("QUERIES_LINKEDIN"),
        queries_computrabajo=_env_list("QUERIES_COMPUTRABAJO"),
        queries_indeed=_env_list("QUERIES_INDEED"),
        queries_glassdoor=_env_list("QUERIES_GLASSDOOR"),
        queries_laborum=_env_list("QUERIES_LABORUM", "python,desarrollador,full stack,backend,java"),
        sample_linkedin=_env_list("SAMPLE_QUERIES_LINKEDIN"),
        sample_indeed=_env_list("SAMPLE_QUERIES_INDEED"),
        sample_computrabajo=_env_list("SAMPLE_QUERIES_COMPUTRABAJO"),
        sample_glassdoor=_env_list("SAMPLE_QUERIES_GLASSDOOR"),
        sample_rotation=_env_float("SAMPLE_ROTATION", 0.33),
    )
    sources = {
        "laborum": _env_bool("ENABLE_LABORUM", True),
        "linkedin": _env_bool("ENABLE_LINKEDIN", True),
        "computrabajo": _env_bool("ENABLE_COMPUTRABAJO", True),
        "indeed": _env_bool("ENABLE_INDEED", True),
        "glassdoor": _env_bool("ENABLE_GLASSDOOR", True),
    }
    premium_hours = [int(h) for h in _env_list("PREMIUM_TICK_HOURS_UTC", "00,12")]
    ia_key = _env("IA_API_KEY")
    # IA_API_KEY puede ser la key directa O el nombre de otra variable de entorno
    if not ia_key or ia_key.startswith("your_"):
        ref = os.environ.get("IA_API_KEY_NAME", "OLLAMA_API_KEY")
        ia_key = os.environ.get(ref, "")
    ia = IaConfig(
        enabled=_env_bool("IA_ENABLED", True),
        model=_env("IA_MODEL", "deepseek-v4-flash"),
        base_url=_env("IA_BASE_URL", "https://ollama.com/v1"),
        api_key=ia_key,
        batch_size=_env_int("IA_BATCH_SIZE", 40),
        timeout=_env_int("IA_TIMEOUT", 120),
        retries=_env_int("IA_RETRIES", 1),
        run_hours_utc=_env_list("IA_RUN_HOURS_UTC", "03"),
    )
    tg = Telegram(
        enabled=_env_bool("TELEGRAM_ENABLED", True),
        bot_token=_env("TELEGRAM_BOT_TOKEN"),
        chat_id=_env("TELEGRAM_CHAT_ID"),
        digest_page_size=_env_int("TELEGRAM_DIGEST_PAGE_SIZE", 5),
        allowed_chats=tuple(
            int(x.strip()) for x in _env("TELEGRAM_ALLOWED_CHATS", "").split(",") if x.strip()
        ),
    )
    alerts = Alerts(
        min_score=_env_int("ALERT_MIN_SCORE", 70),
        max_per_digest=_env_int("ALERT_MAX_PER_DIGEST", 10),
        worth_it_score=_env_int("ALERT_WORTH_IT_SCORE", 60),
    )
    daemon = Daemon(
        interval_min=_env_int("DAEMON_INTERVAL_MIN", 240),
        page_size=_env_int("TELEGRAM_DIGEST_PAGE_SIZE", 5),
        sweep_hours_utc=[int(h) for h in _env_list("DAEMON_SWEEP_HOURS_UTC", "0,4,8,12,16,20")],
    )
    report = ReportCfg(
        enabled=_env_bool("REPORT_ENABLED", True),
        ia_narrative=_env_bool("REPORT_IA_NARRATIVE", True),
        max_salary_samples=_env_int("REPORT_MAX_SALARY_SAMPLES", 60),
        out_dir=PROJECT_ROOT / _env("REPORT_OUT_DIR", "data/reports"),
    )
    cfg = Config(
        profile=profile,
        scoring=scoring,
        search=search,
        sources=sources,
        premium_hours=premium_hours,
        ia=ia,
        telegram=tg,
        alerts=alerts,
        daemon=daemon,
        report=report,
    )
    cfg.data_dir.mkdir(exist_ok=True)
    return cfg