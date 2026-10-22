from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.state import get_setting, set_setting
from shared.config import AppConfig
from shared.schemas import RuleSettings

RULES_KEY = "rules"


def load_rules(db: Session, config: AppConfig) -> RuleSettings:
    raw = get_setting(db, RULES_KEY)
    return RuleSettings.model_validate(raw) if raw else config.rules


def seed_rules(db: Session, config: AppConfig) -> None:
    if get_setting(db, RULES_KEY) is None:
        set_setting(db, RULES_KEY, config.rules.model_dump(mode="json"))
        db.commit()


def save_rules(db: Session, rules: RuleSettings) -> None:
    set_setting(db, RULES_KEY, rules.model_dump(mode="json"))
