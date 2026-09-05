# compat: re-export — eliminar en v6 cuando los imports apunten al paquete nuevo
from __future__ import annotations

from .telegram.render import (  # noqa: F401
    esc, score_emoji, score_style, modality_tag, role_tag, techs_tag, age_tag,
    abbr_loc, salary_tag, lang_tag, compact_label, _attr_esc, _mod_short,
    _role_short, _techs_short, _age_short, table_block, build_digest_text,
    build_buttons, send_digest, log,
)
