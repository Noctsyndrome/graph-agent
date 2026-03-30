from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

DATASET = "kgqa_poc"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "seed_data.cypher"


def q(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def props(mapping: dict[str, object]) -> str:
    return ", ".join(f"{key}: {q(value)}" for key, value in mapping.items())


def emit_node(label: str, row: dict[str, object]) -> str:
    segments = []
    for key, value in row.items():
        if key.endswith("_date") and value is not None:
            segments.append(f"{key}: date({q(value)})")
        else:
            segments.append(f"{key}: {q(value)}")
    return f"CREATE (:{label} {{{', '.join(segments)}}});"


def emit_rel(left_label: str, left_id: str, rel: str, right_label: str, right_id: str) -> str:
    return (
        f"MATCH (a:{left_label} {{id: {q(left_id)}, dataset: {q(DATASET)}}}), "
        f"(b:{right_label} {{id: {q(right_id)}, dataset: {q(DATASET)}}}) "
        f"CREATE (a)-[:{rel} {{dataset: {q(DATASET)}}}]->(b);"
    )


def build_categories() -> list[dict[str, object]]:
    return [
        {"id": "CAT001", "name": "暖通空调系统", "system": "HVAC", "parent_id": "", "level": 1, "dataset": DATASET},
        {"id": "CAT002", "name": "空调主机", "system": "HVAC", "parent_id": "CAT001", "level": 2, "dataset": DATASET},
        {"id": "CAT003", "name": "冷水机组", "system": "HVAC", "parent_id": "CAT002", "level": 3, "dataset": DATASET},
        {"id": "CAT004", "name": "空气源热泵", "system": "HVAC", "parent_id": "CAT002", "level": 3, "dataset": DATASET},
        {"id": "CAT005", "name": "风冷热泵", "system": "HVAC", "parent_id": "CAT002", "level": 3, "dataset": DATASET},
        {"id": "CAT006", "name": "末端系统", "system": "HVAC", "parent_id": "CAT001", "level": 2, "dataset": DATASET},
        {"id": "CAT007", "name": "风机盘管", "system": "HVAC", "parent_id": "CAT006", "level": 3, "dataset": DATASET},
        {"id": "CAT008", "name": "空气处理机组", "system": "HVAC", "parent_id": "CAT006", "level": 3, "dataset": DATASET},
        {"id": "CAT009", "name": "新风机组", "system": "HVAC", "parent_id": "CAT006", "level": 3, "dataset": DATASET},
        {"id": "CAT010", "name": "冷却系统", "system": "HVAC", "parent_id": "CAT001", "level": 2, "dataset": DATASET},
        {"id": "CAT011", "name": "冷却塔", "system": "HVAC", "parent_id": "CAT010", "level": 3, "dataset": DATASET},
        {"id": "CAT012", "name": "冷冻水泵", "system": "HVAC", "parent_id": "CAT010", "level": 3, "dataset": DATASET},
        {"id": "CAT013", "name": "冷却水泵", "system": "HVAC", "parent_id": "CAT010", "level": 3, "dataset": DATASET},
        {"id": "CAT014", "name": "控制系统", "system": "HVAC", "parent_id": "CAT001", "level": 2, "dataset": DATASET},
        {"id": "CAT015", "name": "楼宇自控", "system": "HVAC", "parent_id": "CAT014", "level": 3, "dataset": DATASET},
        {"id": "CAT016", "name": "能源管理系统", "system": "HVAC", "parent_id": "CAT014", "level": 3, "dataset": DATASET},
        {"id": "CAT017", "name": "通风系统", "system": "HVAC", "parent_id": "CAT001", "level": 2, "dataset": DATASET},
        {"id": "CAT018", "name": "排烟风机", "system": "HVAC", "parent_id": "CAT017", "level": 3, "dataset": DATASET},
        {"id": "CAT019", "name": "送风机", "system": "HVAC", "parent_id": "CAT017", "level": 3, "dataset": DATASET},
        {"id": "CAT020", "name": "排风机", "system": "HVAC", "parent_id": "CAT017", "level": 3, "dataset": DATASET},
    ]


def build_customers() -> list[dict[str, object]]:
    rows = [
        ("C001", "万科", "地产", "华南", "A"),
        ("C002", "华润", "商业地产", "华南", "A"),
        ("C003", "招商蛇口", "地产", "华南", "A"),
        ("C004", "龙湖", "商业地产", "华东", "A"),
        ("C005", "保利", "地产", "华北", "A"),
        ("C006", "金地", "地产", "华南", "B"),
        ("C007", "旭辉", "地产", "华东", "B"),
        ("C008", "华侨城", "文旅", "华南", "A"),
        ("C009", "中粮", "商业", "华北", "B"),
        ("C010", "华发", "产业", "华南", "B"),
    ]
    return [
        {"id": cid, "name": name, "industry": industry, "region": region, "level": level, "dataset": DATASET}
        for cid, name, industry, region, level in rows
    ]


def build_projects(customers: list[dict[str, object]]) -> list[dict[str, object]]:
    random.seed(7)
    cities = ["深圳", "上海", "广州", "北京", "杭州", "苏州", "成都", "武汉", "南京", "厦门"]
    types = ["商业", "住宅", "产业园区"]
    statuses = ["规划", "建设中", "运营中"]
    projects: list[dict[str, object]] = []

    special = [
        ("P001", "万科深圳湾商业中心", "商业", "深圳", date(2024, 3, 1), "建设中", 160000, "C001"),
        ("P002", "万科上海虹桥综合体", "商业", "上海", date(2025, 1, 15), "规划", 180000, "C001"),
        ("P003", "万科苏州住宅示范区", "住宅", "苏州", date(2023, 5, 20), "运营中", 98000, "C001"),
        ("P004", "华润深圳前海万象城", "商业", "深圳", date(2024, 6, 18), "建设中", 210000, "C002"),
        ("P005", "招商蛇口上海海上世界", "商业", "上海", date(2022, 9, 8), "运营中", 145000, "C003"),
        ("P006", "龙湖杭州天街", "商业", "杭州", date(2024, 4, 12), "建设中", 132000, "C004"),
    ]
    for pid, name, ptype, city, start, status, area, customer_id in special:
        projects.append(
            {
                "id": pid,
                "name": name,
                "type": ptype,
                "city": city,
                "start_date": start.isoformat(),
                "status": status,
                "area_sqm": area,
                "dataset": DATASET,
                "customer_id": customer_id,
            }
        )

    base_date = date(2021, 1, 1)
    for index in range(7, 31):
        customer = customers[(index - 1) % len(customers)]
        city = cities[(index * 2) % len(cities)]
        ptype = types[index % len(types)]
        start = base_date + timedelta(days=index * 85)
        status = statuses[index % len(statuses)]
        projects.append(
            {
                "id": f"P{index:03d}",
                "name": f"{customer['name']}{city}{ptype}项目{index:02d}",
                "type": ptype,
                "city": city,
                "start_date": start.isoformat(),
                "status": status,
                "area_sqm": 70000 + index * 4500,
                "dataset": DATASET,
                "customer_id": customer["id"],
            }
        )
    return projects


def build_models(categories: list[dict[str, object]]) -> list[dict[str, object]]:
    random.seed(11)
    category_ids = [row["id"] for row in categories if row["level"] == 3]
    models: list[dict[str, object]] = [
        {
            "id": "M001",
            "name": "30XA-300",
            "brand": "开利",
            "cooling_kw": 300,
            "cop": 5.2,
            "refrigerant": "R-22",
            "noise_db": 74,
            "weight_kg": 2300,
            "price_wan": 58,
            "dataset": DATASET,
            "category_id": "CAT003",
        },
        {
            "id": "M002",
            "name": "30XAV-320",
            "brand": "开利",
            "cooling_kw": 320,
            "cop": 6.4,
            "refrigerant": "R-134a",
            "noise_db": 68,
            "weight_kg": 2200,
            "price_wan": 73,
            "dataset": DATASET,
            "category_id": "CAT003",
        },
        {
            "id": "M003",
            "name": "YK-500",
            "brand": "约克",
            "cooling_kw": 500,
            "cop": 6.1,
            "refrigerant": "R-134a",
            "noise_db": 70,
            "weight_kg": 3100,
            "price_wan": 88,
            "dataset": DATASET,
            "category_id": "CAT003",
        },
        {
            "id": "M004",
            "name": "YVAA-420",
            "brand": "约克",
            "cooling_kw": 420,
            "cop": 6.3,
            "refrigerant": "R-513A",
            "noise_db": 69,
            "weight_kg": 2800,
            "price_wan": 83,
            "dataset": DATASET,
            "category_id": "CAT003",
        },
        {
            "id": "M005",
            "name": "EWAD-TZ450",
            "brand": "大金",
            "cooling_kw": 450,
            "cop": 6.2,
            "refrigerant": "R-134a",
            "noise_db": 71,
            "weight_kg": 2950,
            "price_wan": 79,
            "dataset": DATASET,
            "category_id": "CAT003",
        },
        {
            "id": "M006",
            "name": "LSBLX-350",
            "brand": "格力",
            "cooling_kw": 350,
            "cop": 5.8,
            "refrigerant": "R-410A",
            "noise_db": 72,
            "weight_kg": 2500,
            "price_wan": 61,
            "dataset": DATASET,
            "category_id": "CAT003",
        },
    ]

    brands = ["开利", "约克", "大金", "格力", "美的", "海尔"]
    refrigerants = ["R-134a", "R-410A", "R-32", "R-513A", "R-22"]
    prefixes = {
        "开利": "30X",
        "约克": "YK",
        "大金": "EWAD",
        "格力": "LSBLX",
        "美的": "MDV",
        "海尔": "HRF",
    }

    for index in range(7, 51):
        brand = brands[(index - 1) % len(brands)]
        category_id = category_ids[(index - 1) % len(category_ids)]
        cooling_kw = 120 + index * 11
        cop = round(4.8 + (index % 12) * 0.18, 2)
        models.append(
            {
                "id": f"M{index:03d}",
                "name": f"{prefixes[brand]}-{200 + index * 5}",
                "brand": brand,
                "cooling_kw": cooling_kw,
                "cop": cop,
                "refrigerant": refrigerants[index % len(refrigerants)],
                "noise_db": 58 + index % 17,
                "weight_kg": 1100 + index * 37,
                "price_wan": round(18 + index * 1.35, 1),
                "dataset": DATASET,
                "category_id": category_id,
            }
        )
    return models


def build_installations(projects: list[dict[str, object]], models: list[dict[str, object]]) -> list[dict[str, object]]:
    random.seed(19)
    installations: list[dict[str, object]] = []
    preferred = defaultdict(list)
    for model in models:
        preferred[model["brand"]].append(model["id"])

    def add_installation(row_id: int, project_id: str, model_id: str, quantity: int, install_date: str, status: str) -> None:
        installations.append(
            {
                "id": f"I{row_id:03d}",
                "model_id": model_id,
                "project_id": project_id,
                "quantity": quantity,
                "install_date": install_date,
                "status": status,
                "dataset": DATASET,
            }
        )

    forced = [
        ("P001", "M001", 3, "2024-08-01", "active"),
        ("P001", "M002", 2, "2024-08-18", "planned"),
        ("P002", "M003", 4, "2025-04-16", "planned"),
        ("P003", "M006", 2, "2023-06-10", "active"),
        ("P004", "M005", 3, "2024-11-01", "active"),
        ("P005", "M001", 1, "2022-11-10", "active"),
        ("P006", "M004", 2, "2024-09-20", "active"),
    ]
    current = 1
    for project_id, model_id, quantity, install_date, status in forced:
        add_installation(current, project_id, model_id, quantity, install_date, status)
        current += 1

    statuses = ["active", "active", "active", "planned", "maintenance"]
    for project in projects:
        target = 6 + (int(project["id"][1:]) % 3)
        existing = sum(1 for row in installations if row["project_id"] == project["id"])
        for slot in range(existing, target):
            model = models[(slot * 7 + int(project["id"][1:]) * 3) % len(models)]
            quantity = 1 + ((slot + int(project["id"][1:])) % 4)
            install_date = date.fromisoformat(project["start_date"]) + timedelta(days=20 + slot * 30)
            add_installation(
                current,
                project["id"],
                model["id"],
                quantity,
                install_date.isoformat(),
                statuses[(slot + current) % len(statuses)],
            )
            current += 1

    while len(installations) < 200:
        project = projects[(len(installations) * 5) % len(projects)]
        model = models[(len(installations) * 11) % len(models)]
        install_date = date.fromisoformat(project["start_date"]) + timedelta(days=15 + len(installations) % 90)
        add_installation(current, project["id"], model["id"], 1 + current % 5, install_date.isoformat(), "active")
        current += 1

    return installations[:200]


def build_replacements(models: list[dict[str, object]]) -> list[tuple[str, str]]:
    by_category: dict[str, list[dict[str, object]]] = defaultdict(list)
    for model in models:
        by_category[str(model["category_id"])].append(model)

    pairs: list[tuple[str, str]] = [("M001", "M002"), ("M006", "M004")]
    for items in by_category.values():
        ordered = sorted(items, key=lambda row: float(row["cop"]))
        for left, right in zip(ordered, ordered[1:]):
            if float(right["cop"]) > float(left["cop"]):
                pairs.append((str(left["id"]), str(right["id"])))
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def main() -> None:
    customers = build_customers()
    projects = build_projects(customers)
    categories = build_categories()
    models = build_models(categories)
    installations = build_installations(projects, models)
    replacements = build_replacements(models)

    lines = [f"// dataset: {DATASET}"]
    for category in categories:
        lines.append(emit_node("Category", category))
    for customer in customers:
        lines.append(emit_node("Customer", customer))
    for project in projects:
        lines.append(emit_node("Project", {key: value for key, value in project.items() if key != "customer_id"}))
    for model in models:
        lines.append(emit_node("Model", {key: value for key, value in model.items() if key != "category_id"}))
    for installation in installations:
        lines.append(emit_node("Installation", installation))

    for category in categories:
        parent_id = str(category["parent_id"])
        if parent_id:
            lines.append(emit_rel("Category", parent_id, "PARENT_OF", "Category", str(category["id"])))
    for project in projects:
        lines.append(emit_rel("Customer", str(project["customer_id"]), "OWNS_PROJECT", "Project", str(project["id"])))
    for model in models:
        lines.append(emit_rel("Model", str(model["id"]), "BELONGS_TO", "Category", str(model["category_id"])))
    for installation in installations:
        lines.append(emit_rel("Project", str(installation["project_id"]), "HAS_INSTALLATION", "Installation", str(installation["id"])))
        lines.append(emit_rel("Installation", str(installation["id"]), "USES_MODEL", "Model", str(installation["model_id"])))
    for source_id, target_id in replacements:
        lines.append(emit_rel("Model", source_id, "CAN_REPLACE", "Model", target_id))

    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Seed data written to {OUTPUT}")
    print(
        f"customers={len(customers)} projects={len(projects)} categories={len(categories)} "
        f"models={len(models)} installations={len(installations)} replacements={len(replacements)}"
    )


if __name__ == "__main__":
    main()
