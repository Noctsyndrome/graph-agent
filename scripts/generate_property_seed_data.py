from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

DATASET = "property_ops"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "seed_data_property.cypher"


def q(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def emit_node(label: str, row: dict[str, object]) -> str:
    props: list[str] = []
    for key, value in row.items():
        if key.endswith("_date") and value is not None:
            props.append(f"{key}: date({q(value)})")
        else:
            props.append(f"{key}: {q(value)}")
    return f"CREATE (:{label} {{{', '.join(props)}}});"


def emit_rel(left_label: str, left_id: str, rel: str, right_label: str, right_id: str) -> str:
    return (
        f"MATCH (a:{left_label} {{id: {q(left_id)}, dataset: {q(DATASET)}}}), "
        f"(b:{right_label} {{id: {q(right_id)}, dataset: {q(DATASET)}}}) "
        f"CREATE (a)-[:{rel} {{dataset: {q(DATASET)}}}]->(b);"
    )


def build_companies() -> list[dict[str, object]]:
    rows = [
        ("OC001", "万物云", "万科集团", "华南", "大型"),
        ("OC002", "碧桂园服务", "碧桂园集团", "华南", "大型"),
        ("OC003", "龙湖智创生活", "龙湖集团", "华西", "大型"),
        ("OC004", "保利物业", "保利发展", "华南", "大型"),
        ("OC005", "华润万象生活", "华润集团", "华南", "大型"),
        ("OC006", "招商积余", "招商蛇口", "华东", "中型"),
    ]
    return [
        {
            "id": company_id,
            "name": name,
            "parent_group": parent_group,
            "region": region,
            "scale": scale,
            "dataset": DATASET,
        }
        for company_id, name, parent_group, region, scale in rows
    ]


def build_projects() -> list[dict[str, object]]:
    rows = [
        ("OP001", "万象城深圳店", "购物中心", "深圳", 182000, "2019-09-28", "运营中", "OC005"),
        ("OP002", "万象城杭州店", "购物中心", "杭州", 176000, "2020-06-18", "运营中", "OC005"),
        ("OP003", "天街杭州店", "购物中心", "杭州", 158000, "2021-04-30", "运营中", "OC003"),
        ("OP004", "天街重庆店", "购物中心", "重庆", 166000, "2020-11-11", "运营中", "OC003"),
        ("OP005", "保利时光里广州店", "社区商业", "广州", 86000, "2022-08-20", "运营中", "OC004"),
        ("OP006", "保利国际广场上海", "写字楼", "上海", 121000, "2018-05-16", "运营中", "OC004"),
        ("OP007", "万物云前海产业园", "产业园", "深圳", 133000, "2021-03-01", "运营中", "OC001"),
        ("OP008", "万物云武汉智创港", "产业园", "武汉", 118000, "2022-09-10", "运营中", "OC001"),
        ("OP009", "招商花园城南京店", "购物中心", "南京", 142000, "2021-12-12", "运营中", "OC006"),
        ("OP010", "招商智谷厦门园区", "产业园", "厦门", 95000, "2023-03-15", "运营中", "OC006"),
        ("OP011", "碧乐城佛山店", "社区商业", "佛山", 78000, "2022-05-01", "运营中", "OC002"),
        ("OP012", "碧桂园服务武汉广场", "写字楼", "武汉", 105000, "2020-10-18", "运营中", "OC002"),
        ("OP013", "万象城北京店", "购物中心", "北京", 188000, "2019-12-20", "运营中", "OC005"),
        ("OP014", "龙湖西安办公中心", "写字楼", "西安", 112000, "2023-06-01", "运营中", "OC003"),
        ("OP015", "保利成都创意港", "产业园", "成都", 128000, "2022-11-08", "运营中", "OC004"),
        ("OP016", "万物云天津邻里中心", "社区商业", "天津", 64000, "2024-01-20", "运营中", "OC001"),
        ("OP017", "招商积余苏州商务园", "写字楼", "苏州", 98000, "2021-07-07", "运营中", "OC006"),
        ("OP018", "碧桂园服务长沙天地", "社区商业", "长沙", 71000, "2023-02-14", "改造中", "OC002"),
        ("OP019", "华润万象生活青岛项目", "购物中心", "青岛", 136000, "2024-09-01", "筹备中", "OC005"),
        ("OP020", "龙湖合肥天街", "购物中心", "合肥", 149000, "2023-09-28", "运营中", "OC003"),
        ("OP021", "保利珠海办公港", "写字楼", "珠海", 93000, "2020-04-26", "运营中", "OC004"),
    ]
    return [
        {
            "id": project_id,
            "name": name,
            "type": project_type,
            "city": city,
            "total_area_sqm": total_area,
            "opening_date": opening_date,
            "status": status,
            "dataset": DATASET,
            "company_id": company_id,
        }
        for project_id, name, project_type, city, total_area, opening_date, status, company_id in rows
    ]


def build_tenants() -> list[dict[str, object]]:
    rows = [
        ("T001", "星巴克", "餐饮", "国际一线"),
        ("T002", "优衣库", "零售", "国际一线"),
        ("T003", "海底捞", "餐饮", "国内一线"),
        ("T004", "瑞幸咖啡", "餐饮", "国内一线"),
        ("T005", "耐克", "零售", "国际一线"),
        ("T006", "阿迪达斯", "零售", "国际一线"),
        ("T007", "肯德基", "餐饮", "国际一线"),
        ("T008", "必胜客", "餐饮", "国际一线"),
        ("T009", "屈臣氏", "零售", "国际一线"),
        ("T010", "华为体验店", "零售", "国内一线"),
        ("T011", "小米之家", "零售", "国内一线"),
        ("T012", "孩子王", "服务", "国内一线"),
        ("T013", "乐刻运动", "服务", "区域品牌"),
        ("T014", "奈雪的茶", "餐饮", "国内一线"),
        ("T015", "喜茶", "餐饮", "国内一线"),
        ("T016", "MUJI", "零售", "国际一线"),
        ("T017", "H&M", "零售", "国际一线"),
        ("T018", "盒马鲜生", "零售", "国内一线"),
        ("T019", "7-Eleven", "零售", "国际一线"),
        ("T020", "招商银行", "办公", "国内一线"),
        ("T021", "平安科技", "办公", "国内一线"),
        ("T022", "字节跳动", "办公", "国内一线"),
        ("T023", "德勤", "办公", "国际一线"),
        ("T024", "安踏", "零售", "国内一线"),
        ("T025", "茶颜悦色", "餐饮", "区域品牌"),
        ("T026", "本地烘焙工坊", "餐饮", "个体"),
        ("T027", "社区便利店", "零售", "个体"),
        ("T028", "顺丰仓配", "服务", "国内一线"),
        ("T029", "蔚来空间", "零售", "国内一线"),
        ("T030", "联合办公实验室", "办公", "区域品牌"),
    ]
    return [
        {
            "id": tenant_id,
            "name": name,
            "industry": industry,
            "brand_level": brand_level,
            "dataset": DATASET,
        }
        for tenant_id, name, industry, brand_level in rows
    ]


def _space_type_cycle(project_type: str) -> list[tuple[str, str]]:
    if project_type == "购物中心":
        return [("零售", "1F"), ("餐饮", "2F"), ("零售", "3F"), ("零售", "4F"), ("车位", "B1")]
    if project_type == "社区商业":
        return [("零售", "1F"), ("餐饮", "2F"), ("零售", "3F"), ("车位", "B1")]
    if project_type == "写字楼":
        return [("办公", "8F"), ("办公", "12F"), ("办公", "16F"), ("零售", "1F")]
    return [("办公", "3F"), ("办公", "5F"), ("仓储", "B1"), ("车位", "1F")]


def build_spaces(projects: list[dict[str, object]]) -> list[dict[str, object]]:
    spaces: list[dict[str, object]] = []
    current = 1
    for index, project in enumerate(projects):
        type_cycle = _space_type_cycle(str(project["type"]))
        slot_count = 5 if index < 12 else 4
        for slot in range(slot_count):
            space_type, floor = type_cycle[slot % len(type_cycle)]
            area = 68 + (index * 11 + slot * 17) % 210
            unit_price = _space_unit_price(str(project["type"]), str(project["city"]), space_type, slot)
            if str(project["id"]) == "OP001" and slot == 0:
                area = 72
                unit_price = 265
            spaces.append(
                {
                    "id": f"S{current:03d}",
                    "name": _space_name(slot, floor),
                    "floor": floor,
                    "area_sqm": area,
                    "space_type": space_type,
                    "monthly_rent_yuan": unit_price,
                    "dataset": DATASET,
                    "project_id": str(project["id"]),
                }
            )
            current += 1
    return spaces


def _space_name(slot: int, floor: str) -> str:
    if floor.startswith("B"):
        return f"{floor}-{slot + 1:02d}"
    if floor.endswith("F") and floor[:-1].isdigit():
        level = int(floor[:-1])
        prefix = chr(ord("A") + (level % 4))
        return f"{prefix}{level}{slot + 1:02d}"
    return f"U{slot + 1:03d}"


def _space_unit_price(project_type: str, city: str, space_type: str, slot: int) -> int:
    city_weight = {
        "深圳": 120,
        "北京": 110,
        "上海": 105,
        "杭州": 95,
        "广州": 90,
        "重庆": 75,
        "成都": 78,
        "武汉": 72,
        "天津": 68,
        "南京": 82,
        "厦门": 80,
        "苏州": 84,
        "长沙": 66,
        "青岛": 74,
        "合肥": 64,
        "珠海": 76,
        "西安": 70,
        "佛山": 62,
    }
    type_base = {"购物中心": 220, "社区商业": 155, "写字楼": 135, "产业园": 110}
    space_delta = {"零售": 55, "餐饮": 35, "办公": 20, "仓储": -22, "车位": -40}
    return type_base[project_type] + city_weight.get(city, 60) + space_delta.get(space_type, 0) + slot * 9


def build_leases(spaces: list[dict[str, object]], tenants: list[dict[str, object]]) -> list[dict[str, object]]:
    leases: list[dict[str, object]] = []
    active_space_ids = {
        "S001", "S002", "S003", "S006", "S009", "S010", "S013", "S014", "S017", "S018",
        "S021", "S022", "S025", "S026", "S029", "S030", "S033", "S034", "S037", "S038",
        "S041", "S042", "S045", "S046", "S049", "S050", "S053", "S054", "S057", "S058",
        "S061", "S062", "S065", "S066", "S069", "S070", "S073", "S074", "S077", "S078",
    }
    expired_space_ids = {"S004", "S011", "S024", "S031", "S044", "S055", "S080", "S091"}
    terminated_space_ids = {"S005", "S020", "S039", "S060"}
    selected_space_ids = active_space_ids | expired_space_ids | terminated_space_ids

    tenant_plan = [
        ("S001", "T001"), ("S002", "T002"), ("S003", "T004"), ("S004", "T003"), ("S005", "T026"),
        ("S006", "T001"), ("S009", "T014"), ("S010", "T004"), ("S011", "T017"), ("S013", "T002"),
        ("S014", "T003"), ("S017", "T005"), ("S018", "T004"), ("S020", "T025"), ("S021", "T001"),
        ("S022", "T024"), ("S024", "T019"), ("S025", "T006"), ("S026", "T007"), ("S029", "T010"),
        ("S030", "T004"), ("S031", "T020"), ("S033", "T021"), ("S034", "T022"), ("S037", "T023"),
        ("S038", "T030"), ("S039", "T028"), ("S041", "T018"), ("S042", "T004"), ("S044", "T029"),
        ("S045", "T007"), ("S046", "T015"), ("S049", "T001"), ("S050", "T016"), ("S053", "T004"),
        ("S054", "T012"), ("S055", "T027"), ("S057", "T011"), ("S058", "T004"), ("S060", "T013"),
        ("S061", "T021"), ("S062", "T022"), ("S065", "T023"), ("S066", "T020"), ("S069", "T004"),
        ("S070", "T019"), ("S073", "T007"), ("S074", "T014"), ("S077", "T018"), ("S078", "T001"),
        ("S080", "T025"), ("S091", "T026"),
    ]
    tenant_by_space = {space_id: tenant_id for space_id, tenant_id in tenant_plan}
    tenant_ids = [str(tenant["id"]) for tenant in tenants]
    space_by_id = {str(space["id"]): space for space in spaces}
    current = 1
    for space in spaces:
        space_id = str(space["id"])
        if space_id not in selected_space_ids:
            continue
        tenant_id = tenant_by_space.get(space_id)
        if tenant_id is None:
            tenant_id = tenant_ids[(current + int(space_id[1:])) % len(tenant_ids)]
        if space_id in active_space_ids:
            status = "生效中"
            start = date(2025, 1, 1) + timedelta(days=(current * 7) % 45)
            end = start + timedelta(days=365 * (2 + current % 2))
        elif space_id in expired_space_ids:
            status = "已到期"
            start = date(2023, 1, 1) + timedelta(days=(current * 5) % 120)
            end = start + timedelta(days=365)
        else:
            status = "已终止"
            start = date(2024, 1, 1) + timedelta(days=(current * 3) % 90)
            end = start + timedelta(days=240)
        area = float(space["area_sqm"])
        unit_price = float(space["monthly_rent_yuan"])
        monthly_rent = int(round(area * unit_price))
        leases.append(
            {
                "id": f"L{current:03d}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "monthly_rent": monthly_rent,
                "deposit": monthly_rent * 2,
                "rent_free_months": 1 if current % 6 == 0 else 0,
                "status": status,
                "dataset": DATASET,
                "tenant_id": tenant_id,
                "space_id": space_id,
                "occupied": status == "生效中",
            }
        )
        current += 1
    return leases


def build_payments(leases: list[dict[str, object]]) -> list[dict[str, object]]:
    payments: list[dict[str, object]] = []
    current = 1
    overdue_lease_ids = {"L003", "L010", "L018", "L030", "L035", "L042", "L045", "L049"}
    unpaid_lease_ids = {"L011", "L028", "L040", "L051"}
    for lease in leases:
        start = date.fromisoformat(str(lease["start_date"]))
        monthly_rent = int(lease["monthly_rent"])
        lease_id = str(lease["id"])
        for offset in range(3):
            month_start = date(start.year, start.month, 1) + timedelta(days=32 * offset)
            period = month_start.strftime("%Y-%m")
            due_date = date(month_start.year, month_start.month, 10)
            amount = monthly_rent - int(lease["rent_free_months"]) * 500 if offset == 0 else monthly_rent
            if lease["status"] == "生效中":
                if lease_id in overdue_lease_ids:
                    status = "逾期" if offset < 2 else "已付"
                    paid_date = None if offset == 0 else (due_date + timedelta(days=18 + offset)).isoformat()
                elif lease_id in unpaid_lease_ids and offset == 1:
                    status = "未付"
                    paid_date = None
                else:
                    status = "已付"
                    paid_date = (due_date + timedelta(days=2 + offset)).isoformat()
            elif lease["status"] == "已到期":
                status = "已付"
                paid_date = (due_date + timedelta(days=4)).isoformat()
            else:
                status = "逾期" if offset == 0 else "已付"
                paid_date = None if offset == 0 else (due_date + timedelta(days=11)).isoformat()
            payments.append(
                {
                    "id": f"PAY{current:03d}",
                    "period": period,
                    "amount": amount,
                    "due_date": due_date.isoformat(),
                    "paid_date": paid_date,
                    "status": status,
                    "dataset": DATASET,
                    "lease_id": lease_id,
                }
            )
            current += 1
    return payments


def main() -> None:
    companies = build_companies()
    projects = build_projects()
    tenants = build_tenants()
    spaces = build_spaces(projects)
    leases = build_leases(spaces, tenants)
    payments = build_payments(leases)

    lines = [f"// dataset: {DATASET}"]
    for company in companies:
        lines.append(emit_node("OperatingCompany", company))
    for project in projects:
        lines.append(emit_node("OperatingProject", {k: v for k, v in project.items() if k != "company_id"}))
    for space in spaces:
        lines.append(emit_node("Space", {k: v for k, v in space.items() if k != "project_id"}))
    for tenant in tenants:
        lines.append(emit_node("Tenant", tenant))
    for lease in leases:
        lines.append(emit_node("Lease", {k: v for k, v in lease.items() if k not in {"tenant_id", "space_id", "occupied"}}))
    for payment in payments:
        lines.append(emit_node("Payment", {k: v for k, v in payment.items() if k != "lease_id"}))

    for project in projects:
        lines.append(emit_rel("OperatingCompany", str(project["company_id"]), "MANAGES_PROJECT", "OperatingProject", str(project["id"])))
    for space in spaces:
        lines.append(emit_rel("OperatingProject", str(space["project_id"]), "HAS_SPACE", "Space", str(space["id"])))
    for lease in leases:
        lines.append(emit_rel("Tenant", str(lease["tenant_id"]), "HAS_LEASE", "Lease", str(lease["id"])))
        lines.append(emit_rel("Lease", str(lease["id"]), "LEASE_FOR_SPACE", "Space", str(lease["space_id"])))
        if lease["occupied"]:
            lines.append(emit_rel("Space", str(lease["space_id"]), "OCCUPIED_BY", "Tenant", str(lease["tenant_id"])))
    for payment in payments:
        lines.append(emit_rel("Lease", str(payment["lease_id"]), "HAS_PAYMENT", "Payment", str(payment["id"])))

    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Seed data written to {OUTPUT}")
    print(
        f"companies={len(companies)} projects={len(projects)} spaces={len(spaces)} "
        f"tenants={len(tenants)} leases={len(leases)} payments={len(payments)}"
    )


if __name__ == "__main__":
    main()
