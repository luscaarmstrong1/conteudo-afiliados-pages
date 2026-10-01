"""Run the public RSS generator in a checked-out Pages repository."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cloud_generator import SECTIONS, actual_daily_status, ensure_today_batch, load_config, validate_public_tree


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--shopee-only", action="store_true")
    parser.add_argument("--hotmart-only", action="store_true")
    args = parser.parse_args()
    if args.shopee_only and args.hotmart_only:
        parser.error("Selecione apenas uma automação")
    root = args.root.resolve()
    now = datetime.now(ZoneInfo("America/Sao_Paulo"))
    selected = ["shopee"] if args.shopee_only else ["hotmart"] if args.hotmart_only else list(SECTIONS)
    config = load_config(root)
    try:
        results = {name: actual_daily_status(root, name, now.date(), config) if args.status else ensure_today_batch(root, name, now) for name in selected}
        if not args.status:
            validate_public_tree(root, now.date())
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "healthy" if all(row["complete"] for row in results.values()) else "degraded", "date": now.date().isoformat(), "automations": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
