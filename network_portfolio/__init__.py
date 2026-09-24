"""新一代网络建设组合决策引擎的服务端包入口。"""

PROJECT_CODE = "network_portfolio"


def project_info() -> dict[str, str]:
    """返回稳定的项目标识，供运行检查和诊断使用。"""
    return {"code": PROJECT_CODE, "title": "新一代网络建设组合决策引擎"}
