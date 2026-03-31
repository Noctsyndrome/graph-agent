from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

DATASET = "elevator_poc"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "seed_data_elevator.cypher"


def q(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def emit_node(label: str, row: dict[str, object]) -> str:
    segments: list[str] = []
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
    rows = [
        ("CAT001", "电梯系统", "ELEVATOR", "", 1),
        ("CAT002", "乘客电梯", "ELEVATOR", "CAT001", 2),
        ("CAT003", "有机房乘客梯", "ELEVATOR", "CAT002", 3),
        ("CAT004", "无机房乘客梯", "ELEVATOR", "CAT002", 3),
        ("CAT005", "货梯", "ELEVATOR", "CAT001", 2),
        ("CAT006", "载货电梯", "ELEVATOR", "CAT005", 3),
        ("CAT007", "汽车电梯", "ELEVATOR", "CAT005", 3),
        ("CAT008", "自动扶梯", "ELEVATOR", "CAT001", 2),
        ("CAT009", "室内扶梯", "ELEVATOR", "CAT008", 3),
        ("CAT010", "室外扶梯", "ELEVATOR", "CAT008", 3),
        ("CAT011", "特种电梯", "ELEVATOR", "CAT001", 2),
        ("CAT012", "观光电梯", "ELEVATOR", "CAT011", 3),
        ("CAT013", "医用电梯", "ELEVATOR", "CAT011", 3),
        ("CAT014", "消防电梯", "ELEVATOR", "CAT011", 3),
    ]
    return [
        {"id": cid, "name": name, "system": system, "parent_id": parent_id, "level": level, "dataset": DATASET}
        for cid, name, system, parent_id, level in rows
    ]


def build_customers() -> list[dict[str, object]]:
    rows = [
        ("C001", "绿城", "地产", "华东", "A"),
        ("C002", "中海", "地产", "华南", "A"),
        ("C003", "金茂", "商业地产", "华东", "A"),
        ("C004", "世茂", "地产", "华北", "A"),
        ("C005", "融创", "地产", "华西", "B"),
        ("C006", "新城", "商业地产", "华东", "B"),
        ("C007", "雅居乐", "地产", "华南", "B"),
        ("C008", "远洋", "地产", "华北", "B"),
        ("C009", "首开", "地产", "华北", "A"),
        ("C010", "中梁", "地产", "华东", "B"),
    ]
    return [
        {"id": cid, "name": name, "industry": industry, "region": region, "level": level, "dataset": DATASET}
        for cid, name, industry, region, level in rows
    ]


def build_projects(customers: list[dict[str, object]]) -> list[dict[str, object]]:
    cities = ["广州", "北京", "武汉", "重庆", "天津", "厦门"]
    types = ["住宅", "商业", "医院", "酒店", "写字楼"]
    statuses = ["规划", "建设中", "运营中"]
    special = [
        ("P001", "绿城武汉国际医院", "医院", "武汉", "2024-02-12", "建设中", 132000, "C001"),
        ("P002", "绿城北京商务中心", "写字楼", "北京", "2024-08-18", "建设中", 121000, "C001"),
        ("P003", "中海广州天际府", "住宅", "广州", "2023-09-08", "运营中", 98000, "C002"),
        ("P004", "金茂武汉滨江酒店", "酒店", "武汉", "2025-01-20", "规划", 108000, "C003"),
        ("P005", "世茂重庆广场", "商业", "重庆", "2023-05-16", "运营中", 146000, "C004"),
        ("P006", "新城天津吾悦广场", "商业", "天津", "2024-04-10", "建设中", 154000, "C006"),
    ]
    projects: list[dict[str, object]] = []
    for pid, name, ptype, city, start, status, area, customer_id in special:
        projects.append(
            {
                "id": pid,
                "name": name,
                "type": ptype,
                "city": city,
                "start_date": start,
                "status": status,
                "area_sqm": area,
                "dataset": DATASET,
                "customer_id": customer_id,
            }
        )

    base_date = date(2022, 1, 1)
    for index in range(7, 31):
        customer = customers[(index - 1) % len(customers)]
        city = cities[index % len(cities)]
        ptype = types[index % len(types)]
        start = base_date + timedelta(days=index * 73)
        status = statuses[index % len(statuses)]
        projects.append(
            {
                "id": f"P{index:03d}",
                "name": f"{customer['name']}{city}{ptype}项目{index:02d}",
                "type": ptype,
                "city": city,
                "start_date": start.isoformat(),
                "status": status,
                "area_sqm": 82000 + index * 4300,
                "dataset": DATASET,
                "customer_id": customer["id"],
            }
        )
    return projects


def build_models(categories: list[dict[str, object]]) -> list[dict[str, object]]:
    category_ids = [row["id"] for row in categories if row["level"] == 3]
    base_models = [
        ("M001", "GeN2-MR", "奥的斯", 1000, 3.5, "永磁同步", 48, 32, 118, "CAT004"),
        ("M002", "MONOSPACE-1200", "三菱", 1200, 4.0, "永磁同步", 46, 36, 132, "CAT004"),
        ("M003", "KONE-N-Mono", "通力", 1000, 3.0, "异步变频", 49, 30, 109, "CAT003"),
        ("M004", "META200", "蒂森克虏伯", 1600, 2.5, "液压", 52, 18, 96, "CAT006"),
        ("M005", "GVF-II", "日立", 1350, 3.5, "永磁同步", 47, 34, 126, "CAT003"),
        ("M006", "Schindler-5500", "迅达", 1000, 2.5, "异步变频", 50, 28, 104, "CAT012"),
    ]
    models = [
        {
            "id": mid,
            "name": name,
            "brand": brand,
            "load_kg": load_kg,
            "speed_ms": speed_ms,
            "drive_type": drive_type,
            "noise_db": noise_db,
            "floors": floors,
            "price_wan": price_wan,
            "dataset": DATASET,
            "category_id": category_id,
        }
        for mid, name, brand, load_kg, speed_ms, drive_type, noise_db, floors, price_wan, category_id in base_models
    ]

    brand_defs = [
        ("奥的斯", "OTIS", "CAT004"),
        ("三菱", "MITS", "CAT003"),
        ("通力", "KONE", "CAT004"),
        ("蒂森克虏伯", "TK", "CAT006"),
        ("日立", "HIT", "CAT013"),
        ("迅达", "SCH", "CAT012"),
    ]
    drive_types = ["永磁同步", "异步变频", "液压"]

    for index in range(7, 49):
        brand, prefix, fallback_category = brand_defs[(index - 1) % len(brand_defs)]
        category_id = category_ids[(index - 1) % len(category_ids)] if index % 4 else fallback_category
        models.append(
            {
                "id": f"M{index:03d}",
                "name": f"{prefix}-{800 + index * 15}",
                "brand": brand,
                "load_kg": 800 + (index % 9) * 150,
                "speed_ms": round(1.5 + (index % 7) * 0.5, 1),
                "drive_type": drive_types[index % len(drive_types)],
                "noise_db": 44 + (index % 12),
                "floors": 12 + index % 28,
                "price_wan": round(62 + index * 2.1, 1),
                "dataset": DATASET,
                "category_id": category_id,
            }
        )
    return models


def build_installations(projects: list[dict[str, object]], models: list[dict[str, object]]) -> list[dict[str, object]]:
    forced = [
        ("P001", "M001", 4, "2024-07-01", "active"),
        ("P001", "M004", 1, "2024-07-15", "planned"),
        ("P002", "M002", 6, "2024-10-10", "active"),
        ("P003", "M003", 5, "2023-12-16", "active"),
        ("P004", "M006", 3, "2025-05-03", "planned"),
        ("P005", "M005", 4, "2023-08-18", "active"),
        ("P006", "M003", 8, "2024-09-09", "active"),
        ("P006", "M002", 3, "2024-10-21", "planned"),
    ]
    installations: list[dict[str, object]] = []

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

    current = 1
    for project_id, model_id, quantity, install_date, status in forced:
        add_installation(current, project_id, model_id, quantity, install_date, status)
        current += 1

    statuses = ["active", "active", "active", "planned", "maintenance"]
    for project in projects:
        target = 4 + (int(project["id"][1:]) % 4)
        existing = sum(1 for row in installations if row["project_id"] == project["id"])
        for slot in range(existing, target):
            model = models[(slot * 5 + int(project["id"][1:]) * 2) % len(models)]
            quantity = 1 + ((slot + int(project["id"][1:])) % 6)
            install_date = date.fromisoformat(str(project["start_date"])) + timedelta(days=18 + slot * 27)
            add_installation(
                current,
                str(project["id"]),
                str(model["id"]),
                quantity,
                install_date.isoformat(),
                statuses[(slot + current) % len(statuses)],
            )
            current += 1

    return installations


def build_replacements(models: list[dict[str, object]]) -> list[tuple[str, str]]:
    by_brand: dict[str, list[dict[str, object]]] = defaultdict(list)
    for model in models:
        by_brand[str(model["brand"])].append(model)

    pairs: list[tuple[str, str]] = [("M004", "M001"), ("M001", "M002"), ("M006", "M005")]
    for items in by_brand.values():
        ordered = sorted(items, key=lambda row: (float(row["speed_ms"]), float(row["load_kg"])))
        for left, right in zip(ordered, ordered[1:]):
            if float(right["speed_ms"]) >= float(left["speed_ms"]):
                pairs.append((str(left["id"]), str(right["id"])))

    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pair in pairs:
        if pair in seen:
            continue
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
