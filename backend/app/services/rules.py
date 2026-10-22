from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.state import get_setting, set_setting
from shared.config import AppConfig
from shared.schemas import RuleSettings

RULES_KEY = "rules"
DEFAULTS_VERSION_KEY = "rules_defaults_version"
DEFAULTS_VERSION = 2


def load_rules(db: Session, config: AppConfig) -> RuleSettings:
    raw = get_setting(db, RULES_KEY)
    return RuleSettings.model_validate(raw) if raw else config.rules


def seed_rules(db: Session, config: AppConfig) -> None:
    raw = get_setting(db, RULES_KEY)
    if raw is None:
        set_setting(db, RULES_KEY, config.rules.model_dump(mode="json"))
    elif int(get_setting(db, DEFAULTS_VERSION_KEY, 1)) < DEFAULTS_VERSION:
        set_setting(db, RULES_KEY, migrate_defaults(raw, config).model_dump(mode="json"))
    else:
        return
    set_setting(db, DEFAULTS_VERSION_KEY, DEFAULTS_VERSION)
    db.commit()


def migrate_defaults(raw: dict, config: AppConfig) -> RuleSettings:
    raw = {k: dict(v) for k, v in raw.items()}
    zone, smoking = raw.get("zone_intrusion", {}), raw.get("smoking", {})
    if zone.get("min_observations", 1) == 1:
        zone["min_observations"] = config.rules.zone_intrusion.min_observations
    if (smoking.get("k"), smoking.get("m")) == (2, 5):
        smoking["k"], smoking["m"] = config.rules.smoking.k, config.rules.smoking.m
    raw["zone_intrusion"], raw["smoking"] = zone, smoking
    return RuleSettings.model_validate(raw)


def save_rules(db: Session, rules: RuleSettings) -> None:
    set_setting(db, RULES_KEY, rules.model_dump(mode="json"))
