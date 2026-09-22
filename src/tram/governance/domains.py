"""十大知识域 × 五过程组的确定性归属 - PMBOK 裁剪版.

线路图子过程、文件车厢归属、会话前导共用这一份词表：Tram 治下的
每一个工件都答得出「属于哪个过程组 · 哪个知识域 · 哪个子过程」。
分类永远是确定性的模式匹配，LLM 不进判定路径。
"""

from __future__ import annotations

from typing import Any

from tram.governance.intent_guard import PROCUREMENT_PATTERNS, path_matches

# 知识域徽记（单字）——线路图、文件徽章、前导词共用
KNOWLEDGE_AREAS: dict[str, str] = {
    "int": "整合",
    "sco": "范围",
    "sch": "进度",
    "cost": "成本",
    "qa": "质量",
    "res": "资源",
    "com": "沟通",
    "risk": "风险",
    "proc": "采购",
    "stak": "相关方",
}

# 各过程组的子过程（裁剪版：保 PMBOK 骨架，留 Tram 实治的部分；十大域全覆盖）
SUBPROCESSES: dict[str, list[list[str]]] = {
    "initiating": [
        ["int", "制定项目章程"],
        ["stak", "识别相关方"],
    ],
    "planning": [
        ["sco", "定义范围·WBS"],
        ["sch", "活动排序与工期"],
        ["cost", "制定预算"],
        ["qa", "规划质量管理"],
        ["risk", "规划风险应对"],
    ],
    "executing": [
        ["int", "指导与管理工作"],
        ["int", "管理项目知识"],
        ["res", "获取资源"],
        ["qa", "管理质量"],
        ["com", "管理沟通"],
        ["proc", "实施采购"],
    ],
    "monitoring": [
        ["int", "整体变更控制"],
        ["sco", "确认范围"],
        ["sch", "控制进度"],
        ["cost", "控制成本"],
        ["qa", "控制质量"],
        ["risk", "监督风险"],
    ],
    "closing": [
        ["int", "结束项目或阶段"],
    ],
}

PHASE_GROUPS = {
    "initiating": "启动",
    "planning": "规划",
    "executing": "执行",
    "monitoring": "监控",
    "closing": "收尾",
}

# 文件归属规则：先命中先得（.tram 证据 > 依赖清单 > 测试 > CI > 文档 > 工具链 > 默认产品工作）
_DOMAIN_RULES: list[tuple[list[str], str, str, str]] = [
    ([".tram/**", ".tram/*"], "int", "管理项目知识", "executing"),
    (PROCUREMENT_PATTERNS, "proc", "实施采购", "executing"),
    (
        [
            "**/test_*.py",
            "**/*_test.py",
            "**/conftest.py",
            "tests/**",
            "tests/*",
            "**/tests/**",
            "**/spec/**",
        ],
        "qa",
        "管理质量",
        "executing",
    ),
    (
        [
            ".github/workflows/**",
            ".github/workflows/*",
            ".gitlab-ci.yml",
            "Jenkinsfile",
            ".circleci/**",
        ],
        "qa",
        "控制质量",
        "monitoring",
    ),
    (["docs/**", "docs/*", "**/*.md", "**/*.rst", "*.md", "*.rst"], "com", "管理沟通", "executing"),
    (
        ["scripts/**", "scripts/*", "Makefile", "tox.ini", ".pre-commit-config.yaml", "justfile"],
        "res",
        "获取资源",
        "executing",
    ),
]

_DEFAULT_DOMAIN = ("int", "指导与管理工作", "executing")


def classify_path(rel: str) -> dict[str, str]:
    """相对路径 -> {badge, area, process, group}（过程组中文名一并给出）。"""
    posix = rel.replace("\\", "/")
    for patterns, area_key, process, group in _DOMAIN_RULES:
        if path_matches(posix, patterns):
            return {
                "badge": _badge(area_key),
                "area": KNOWLEDGE_AREAS[area_key],
                "process": process,
                "group": PHASE_GROUPS[group],
            }
    badge, process, group = _DEFAULT_DOMAIN
    return {
        "badge": badge,
        "area": KNOWLEDGE_AREAS["int"],
        "process": process,
        "group": PHASE_GROUPS[group],
    }


def domains_payload() -> dict[str, Any]:
    """/api/domains 的载荷：徽记表 + 各过程组子过程（线路图渲染用）。"""
    return {
        "areas": [{"key": k, "badge": _badge(k), "name": v} for k, v in KNOWLEDGE_AREAS.items()],
        "subprocesses": {
            phase: [
                {"badge": _badge(key), "area": KNOWLEDGE_AREAS[key], "process": proc}
                for key, proc in procs
            ]
            for phase, procs in SUBPROCESSES.items()
        },
    }


def _badge(area_key: str) -> str:
    return KNOWLEDGE_AREAS[area_key][0]
