from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.bridge import sanitize_companion_expression_decision  # noqa: E402
from core.models import SessionContext  # noqa: E402
from core.service import MemoryCompanionService  # noqa: E402


def v2_decision(**updates):
    value = {
        "contract": "companion_interaction_expression.v2",
        "expression_band": "warm",
        "followup": True,
        "allowed_behaviors": ["reply", "support", "followup"],
        "safety_mode": "normal",
        "blocker": None,
        "reason_codes": ["interaction_band_applied"],
        "pacing": "steady",
        "directness": "natural",
        "validation_style": "support_first",
        "self_disclosure": "light",
        "humor_mode": "light",
        "topic_initiative": "followup",
    }
    value.update(updates)
    return value


class EmotionE8ExpressionAuthorityTests(unittest.TestCase):
    def test_v2_dimensions_are_validated_and_consumed_read_only(self) -> None:
        accepted = sanitize_companion_expression_decision(v2_decision())
        self.assertEqual("accepted", accepted["status"])
        self.assertEqual("support_first", accepted["decision"]["validation_style"])
        ctx = SessionContext(scope="private")
        req = SimpleNamespace(_private_companion_expression_decision=v2_decision())
        MemoryCompanionService._apply_companion_expression_decision(ctx, req=req)
        self.assertEqual("companion_interaction_expression.v2", ctx.companion_expression_contract)
        self.assertEqual("light", ctx.companion_expression_humor_mode)
        self.assertEqual("followup", ctx.companion_expression_topic_initiative)

    def test_invalid_dimension_fails_closed_and_v1_remains_compatible(self) -> None:
        self.assertEqual("invalid", sanitize_companion_expression_decision(v2_decision(humor_mode="override_safety"))["status"])
        legacy = v2_decision(contract="companion_interaction_expression.v1")
        for key in ("pacing", "directness", "validation_style", "self_disclosure", "humor_mode", "topic_initiative"):
            legacy.pop(key)
        accepted = sanitize_companion_expression_decision(legacy)
        self.assertEqual("accepted", accepted["status"])
        self.assertNotIn("humor_mode", accepted["decision"])


if __name__ == "__main__":
    unittest.main()
