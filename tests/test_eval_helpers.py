from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_run_eval_module():
    module_path = Path(__file__).resolve().parents[1] / "eval" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_keyword_matching_supports_column_aliases() -> None:
    module = _load_run_eval_module()
    alias_lookup = module._build_alias_lookup(
        {
            "型号": {"zh": ["设备名称", "机型"], "en": ["model_name"]},
            "项目": {"zh": ["项目名称"], "en": ["project_name"]},
        }
    )
    text = "结果预览: [{'设备名称': '30XA-300', '项目名称': '万科深圳湾商业中心'}]"
    assert module._keyword_matches("型号", text, alias_lookup) is True
    assert module._keyword_matches("项目", text, alias_lookup) is True
    assert module._keyword_matches("品牌", text, alias_lookup) is False
