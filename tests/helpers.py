"""测试共用的样例数据与工厂函数。"""

from __future__ import annotations

from network_portfolio.infrastructure.memory import build_service


def make_service():
    """每个用例使用独立的服务实例，承诺与版本互不影响。"""
    return build_service()


def stage(name, year, cost, coverage="0", industry="0"):
    return {"name": name, "year": year, "cost": cost, "coverage": coverage, "industry": industry}


def project(pid, *, region="测试区", maturity="1", exit_loss="0", category="测试", stages):
    return {
        "project_id": pid,
        "name": pid,
        "category": category,
        "region": region,
        "maturity": maturity,
        "exit_loss": exit_loss,
        "stages": stages,
    }


def mini_payload(
    projects,
    *,
    budget,
    min_coverage="0",
    caps=None,
    prereqs=None,
    mutex=None,
    shared=None,
    weights=None,
):
    """构造最小可用的假设数据；budget 形如 {2027: "100"}。"""
    return {
        "label": "测试假设",
        "projects": projects,
        "shared_benefits": shared or [],
        "constraints": {
            "annual_budget": {str(year): amount for year, amount in budget.items()},
            "min_coverage": min_coverage,
            "regional_caps": caps or {},
            "prerequisites": prereqs or [],
            "mutex_groups": mutex or [],
        },
        "weights": weights or {"coverage": "1", "industry": "1", "cost": "1"},
    }


def sample_payload():
    """四类新一代网络候选工程的完整样例（移动增强/万兆光网/卫星补盲/算力节点）。"""
    return {
        "label": "2027-2029 新一代网络建设候选池",
        "projects": [
            project(
                "MOB-ENH",
                category="移动增强",
                region="东部",
                maturity="0.9",
                exit_loss="30",
                stages=[
                    stage("一期", 2027, "100", "40", "20"),
                    stage("二期", 2028, "120", "50", "30"),
                ],
            ),
            project(
                "OPT-10G",
                category="万兆光网",
                region="东部",
                maturity="0.8",
                exit_loss="40",
                stages=[
                    stage("一期", 2027, "150", "60", "50"),
                    stage("二期", 2029, "160", "70", "60"),
                ],
            ),
            project(
                "SAT-FILL",
                category="卫星补盲",
                region="西部",
                maturity="0.6",
                exit_loss="20",
                stages=[stage("一期", 2028, "80", "90", "10")],
            ),
            project(
                "COMPUTE",
                category="算力节点",
                region="西部",
                maturity="0.85",
                exit_loss="25",
                stages=[
                    stage("一期", 2028, "90", "10", "80"),
                    stage("二期", 2029, "90", "10", "90"),
                ],
            ),
        ],
        "shared_benefits": [
            {
                "group_id": "G-ACCESS",
                "project_ids": ["MOB-ENH", "OPT-10G"],
                "coverage_overlap": "15",
                "industry_overlap": "5",
            }
        ],
        "constraints": {
            "annual_budget": {"2027": "300", "2028": "300", "2029": "300"},
            "min_coverage": "80",
            "regional_caps": {"东部": "500", "西部": "400"},
            "prerequisites": [["COMPUTE", "OPT-10G"]],
            "mutex_groups": [],
        },
        "weights": {"coverage": "2", "industry": "1", "cost": "1"},
    }
