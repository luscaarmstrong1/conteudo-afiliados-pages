"""Public-only, deterministic RSS repair shared by Windows and GitHub Actions."""
from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from html import escape
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
SECTIONS = {"shopee": "achadinhos-conteudo", "hotmart": "desenho-manga-conteudo"}
DESTINATIONS = {"shopee": "s.shopee.com.br", "hotmart": "go.hotmart.com"}
REGISTRY_FIELDS = ["daily_key", "automation", "date", "guid", "post_path", "go_path", "affiliate_url", "image_url"]
HISTORY_FIELDS = ["daily_key", "automation", "publish_date", "title", "description", "image_url", "guid"]
ANGLES_SHOPEE = [
    "organizar melhor a rotina", "aproveitar melhor o espaço", "simplificar uma tarefa",
    "cuidar dos detalhes", "deixar o dia mais prático", "comparar opções com calma",
    "renovar um cantinho", "ganhar tempo no dia a dia", "montar um espaço funcional",
    "resolver um problema comum", "melhorar a organização", "facilitar o uso diário",
]
ANGLES_HOTMART = [
    "treinar o olhar", "praticar formas básicas", "observar proporções",
    "experimentar novos traços", "desenvolver um personagem", "revisar os fundamentos",
    "estudar luz e sombra", "melhorar a composição", "praticar com referência",
    "registrar a evolução", "testar outra técnica", "desenhar com mais intenção",
]
FILENAME_THEME = re.compile(r"(?:^|\s)(?:img|image|foto|dsc|gemini|generated|v0|\d{3,})(?:\s|$)", re.I)


def sanitize_theme(theme: str, fallback: str = "referência de desenho") -> str:
    value = unicodedata.normalize("NFKC", str(theme or "")).replace("_", " ").replace("-", " ")
    value = " ".join(value.split()).strip(" .,:;~()[]{}")
    if not value or "�" in value or FILENAME_THEME.search(value) or not any(char.isalpha() for char in value):
        value = fallback
    return value[:70].strip()


def _normalized_theme(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(char for char in value if not unicodedata.combining(char)).split())


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    site_root = next(
        (parent for parent in path.parents if (parent / "automation_runtime" / "publication_config.json").exists()),
        None,
    )
    staging_dir = site_root.parent if site_root is not None else path.parent
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=staging_dir, prefix="pinterest-write-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def append_rows(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    output = StringIO(newline="")
    output.write(existing)
    if existing and not existing.endswith(("\r", "\n")):
        output.write("\n")
    writer = csv.DictWriter(output, fieldnames=fields)
    if not existing:
        writer.writeheader()
    writer.writerows(rows)
    write_if_changed(path, output.getvalue())


def text_of(item: ET.Element, tag: str) -> str:
    return item.findtext(tag, default="").strip()


def rss_items(path: Path) -> list[ET.Element]:
    if not path.exists():
        raise ValueError(f"Feed ausente: {path}")
    return ET.parse(path).getroot().findall("./channel/item")


def item_date(item: ET.Element) -> date:
    return parsedate_to_datetime(text_of(item, "pubDate")).astimezone(TZ).date()


def daily_key(automation: str, day: date, sequence: int) -> str:
    return f"{automation}|{day.isoformat()}|{sequence:03d}"


def key_from_guid(guid: str, automation: str, day: date) -> str | None:
    match = re.search(rf"/{automation}-{day:%Y%m%d}-(\d{{3}})\.html$", guid)
    return daily_key(automation, day, int(match.group(1))) if match else None


def load_config(root: Path) -> dict:
    config = json.loads((root / "automation_runtime" / "publication_config.json").read_text(encoding="utf-8"))
    if config["timezone"] != "America/Sao_Paulo" or int(config["phase"]) not in (1, 2, 3):
        raise ValueError("Configuração de timezone ou fase inválida")
    return config


def target(config: dict) -> int:
    return int(config["phase_targets"][str(config["phase"])])


def actual_daily_status(root: Path, automation: str, day: date, config: dict | None = None) -> dict:
    config = config or load_config(root)
    section = root / SECTIONS[automation]
    all_today_items = [item for item in rss_items(section / "feed.xml") if item_date(item) == day]
    today_items = [item for item in all_today_items if key_from_guid(text_of(item, "guid"), automation, day)]
    keys = {key_from_guid(text_of(item, "guid"), automation, day) for item in today_items}
    keys.discard(None)
    registry = read_rows(root / "automation_runtime" / "public_registry.csv")
    history = read_rows(root / "automation_runtime" / "editorial_history.csv")
    base = config["public_base_url"].rstrip("/") + "/"
    go_count = sum((root / text_of(item, "guid").removeprefix(base)).is_file() for item in today_items)
    count = len(today_items)
    goal = target(config)
    return {
        "date": day.isoformat(), "feed_count": count, "legacy_today_count": len(all_today_items) - count,
        "registry_count": sum(row.get("daily_key") in keys for row in registry),
        "history_count": sum(row.get("daily_key") in keys for row in history),
        "go_count": go_count, "target": goal, "missing": max(0, goal - count),
        "complete": count >= goal and go_count == count and
        all(any(row.get("daily_key") == key for row in registry) for key in keys) and
        all(any(row.get("daily_key") == key for row in history) for key in keys),
    }


def validate_no_future_pubdates(items: list[ET.Element], now: datetime) -> None:
    future = [item for item in items if parsedate_to_datetime(text_of(item, "pubDate")).astimezone(TZ) > now]
    if future:
        raise ValueError(f"pubDate futuro: {len(future)} item(ns)")


def _safe_catalog(root: Path, automation: str) -> list[dict]:
    catalog = json.loads((root / "automation_runtime" / f"catalog_{automation}.json").read_text(encoding="utf-8"))
    if not isinstance(catalog, list) or not catalog:
        raise ValueError(f"Catálogo público vazio: {automation}")
    for entry in catalog:
        affiliate = urlparse(entry["affiliate_url"])
        if affiliate.scheme != "https" or affiliate.hostname != DESTINATIONS[automation]:
            raise ValueError(f"Destino inválido no catálogo {automation}")
        if not entry.get("images"):
            raise ValueError(f"Item sem imagem no catálogo {automation}")
        for image in entry["images"]:
            parsed = urlparse(image["url"])
            if parsed.scheme != "https" or parsed.hostname != "res.cloudinary.com":
                raise ValueError(f"Imagem fora do Cloudinary no catálogo {automation}")
            if not image.get("semantic_theme"):
                raise ValueError(f"Imagem sem semantic_theme no catálogo {automation}")
            if automation == "shopee":
                allowed = {_normalized_theme(value) for value in entry.get("allowed_themes", [])}
                if _normalized_theme(image["semantic_theme"]) not in allowed:
                    raise ValueError("Tema da imagem incompatível com o produto Shopee")
    return catalog


def _image_url(url: str) -> str:
    transform = "c_pad,w_1000,h_1500,b_auto,q_auto:eco"
    if transform in url:
        return url
    return url.replace("/image/upload/", f"/image/upload/{transform}/", 1)


def _recent_images(items: list[ET.Element], history: list[dict], window: int) -> set[str]:
    urls = []
    for item in items:
        enclosure = item.find("enclosure")
        if enclosure is not None:
            urls.append(enclosure.get("url", ""))
    urls.extend(row.get("image_url", "") for row in reversed(history))
    return set(urls[:window])


def _choose_image(entry: dict, used: set[str], seed: str) -> tuple[str, str]:
    images = entry["images"]
    start = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % len(images)
    ordered = images[start:] + images[:start]
    image = next((image for image in ordered if _image_url(image["url"]) not in used), ordered[0])
    fallback = "referência de desenho" if "stable_course_id" in entry else entry.get("primary_intent", "produto útil")
    return _image_url(image["url"]), sanitize_theme(image.get("semantic_theme", ""), fallback)


def _copy(automation: str, entry: dict, theme: str, sequence: int, day: date, history: list[dict]) -> tuple[str, str]:
    ordinal = day.toordinal() * 20 + sequence
    angles = ANGLES_SHOPEE if automation == "shopee" else ANGLES_HOTMART
    angle = angles[ordinal % len(angles)]
    topic = theme.replace("_", " ").strip().capitalize()
    name = entry["name"].strip()
    if automation == "shopee":
        title = f"{topic}: {name} para {angle}"
        description = (
            f"Uma ideia de {theme.replace('_', ' ')} com {name} para {angle}. "
            f"Observe o tamanho, o acabamento e como o produto se encaixa no seu espaço antes de escolher. "
            "Esta publicação pode conter link de afiliado; confira os detalhes na página da oferta."
        )
    else:
        title = f"{topic}: como {angle} com {name}"
        description = (
            f"Use esta referência de {theme.replace('_', ' ')} para {angle} em uma prática de desenho. "
            f"Comece pelas formas simples, compare as proporções e revise os traços aos poucos. "
            f"O curso {name} pode ajudar a organizar os estudos; o link da página é de afiliado."
        )
    title = title[:95].rstrip(" :,-")
    if title in {row.get("title") for row in history}:
        title = f"{title[:75].rstrip()} | Prática {ordinal}" if automation == "hotmart" else f"{title[:75].rstrip()} | Ideia {ordinal}"
    if description in {row.get("description") for row in history}:
        description = f"{description} Nesta seleção, o foco é {angle} (ideia {ordinal})."
    if not 180 <= len(description) <= 420:
        raise ValueError(f"Descrição fora do limite: {automation}")
    return title, description


def _page_html(config: dict, automation: str, title: str, description: str, image: str, post_url: str, go_url: str) -> str:
    meta = config["pinterest_domain_verify_meta"]
    if automation == "shopee":
        meta += "\n  " + config["achadinhos_domain_verify_meta"]
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{escape(description[:160], quote=True)}">
<link rel="canonical" href="{escape(post_url, quote=True)}">
<link rel="stylesheet" href="{escape(config['public_base_url'], quote=True)}/styles.css">
<meta property="og:image" content="{escape(image, quote=True)}">
{meta}<title>{escape(title)}</title></head><body>
<main class="policy"><h1>{escape(title)}</h1><img src="{escape(image, quote=True)}" alt="{escape(title, quote=True)}" width="1000" height="1500">
<p>{escape(description)}</p><p>Esta página pode conter link de afiliado.</p>
<p><a class="button" href="{escape(go_url, quote=True)}" rel="sponsored nofollow noopener">Ver oferta</a></p></main></body></html>\n'''


def _go_html(config: dict, automation: str, entry: dict, go_url: str) -> str:
    destination = entry["affiliate_url"]
    js_url = json.dumps(destination, ensure_ascii=True).replace("<", "\\u003c")
    meta = config["pinterest_domain_verify_meta"]
    if automation == "shopee":
        meta += "\n" + config["achadinhos_domain_verify_meta"]
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="robots" content="noindex,follow"><link rel="canonical" href="{escape(go_url, quote=True)}">
<meta http-equiv="refresh" content="0;url={escape(destination, quote=True)}">
{meta}<title>Ir para a oferta</title></head><body>
<main class="policy"><h1>Ir para a oferta</h1><p>Voce sera redirecionado para a oferta. Este é um link de afiliado.</p>
<p><a class="button" href="{escape(destination, quote=True)}" rel="sponsored nofollow noopener">Abrir oferta</a></p></main>
<script>window.location.replace({js_url});</script></body></html>\n'''


def _new_item(title: str, description: str, guid: str, image: str, published: datetime) -> ET.Element:
    item = ET.Element("item")
    for tag, value in (("title", title), ("link", guid), ("description", description), ("pubDate", format_datetime(published)), ("guid", guid)):
        ET.SubElement(item, tag).text = value
    item.find("guid").set("isPermaLink", "true")
    ET.SubElement(item, "enclosure", {"url": image, "type": "image/jpeg"})
    ET.SubElement(item, "{http://purl.org/rss/1.0/modules/content/}encoded").text = f'<p><img src="{escape(image, quote=True)}" alt="{escape(title, quote=True)}"></p><p>{escape(description)}</p>'
    return item


def _write_feed(path: Path, items: list[ET.Element], max_items: int) -> None:
    ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")
    tree = ET.parse(path)
    channel = tree.getroot().find("channel")
    assert channel is not None
    for old in list(channel.findall("item")):
        channel.remove(old)
    by_guid = {text_of(item, "guid"): item for item in items}
    ordered = sorted(by_guid.values(), key=lambda item: parsedate_to_datetime(text_of(item, "pubDate")), reverse=True)
    for item in ordered[:max_items]:
        channel.append(item)
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tree.getroot(), encoding="unicode")
    write_if_changed(path, xml)
    write_if_changed(path.with_name("rss.xml"), xml)


def _update_sitemap(root: Path, config: dict, rows: list[dict[str, str]]) -> bool:
    path = root / "sitemap.xml"
    if not path.exists() or not rows:
        return False
    tree = ET.parse(path)
    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", namespace)
    existing = {node.text for node in tree.getroot().iter(f"{{{namespace}}}loc")}
    changed = False
    for row in rows:
        for relative in (row["post_path"], row["go_path"]):
            url = f"{config['public_base_url']}/{relative}"
            if url not in existing:
                ET.SubElement(ET.SubElement(tree.getroot(), f"{{{namespace}}}url"), f"{{{namespace}}}loc").text = url
                existing.add(url)
                changed = True
    if changed:
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tree.getroot(), encoding="unicode")
        write_if_changed(path, xml)
    return changed


def ensure_today_batch(root: Path, automation: str, now: datetime | None = None) -> dict:
    config = load_config(root)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    day = now.date()
    section = root / SECTIONS[automation]
    feed_path = section / "feed.xml"
    items = rss_items(feed_path)
    feed_changed = False
    for item in items:
        published = parsedate_to_datetime(text_of(item, "pubDate")).astimezone(TZ)
        if published > now:
            item.find("pubDate").text = format_datetime(now)
            feed_changed = True
    goal = target(config)
    existing_today = [item for item in items if item_date(item) == day and key_from_guid(text_of(item, "guid"), automation, day)]
    history_path = root / "automation_runtime" / "editorial_history.csv"
    registry_path = root / "automation_runtime" / "public_registry.csv"
    history = read_rows(history_path)
    registry = read_rows(registry_path)
    known_keys = {row["daily_key"] for row in registry}
    history_keys = {row["daily_key"] for row in history}
    new_registry: list[dict[str, str]] = []
    new_history: list[dict[str, str]] = []
    catalog = _safe_catalog(root, automation)
    existing_keys = {key_from_guid(text_of(item, "guid"), automation, day) for item in existing_today}
    existing_keys.discard(None)
    used_images = _recent_images(items, history, int(config["recent_image_window"]))
    changed = feed_changed

    history_by_key = {row.get("daily_key"): row for row in history}
    for row in registry:
        key = row.get("daily_key", "")
        if row.get("automation") != automation or row.get("date") != day.isoformat() or key in existing_keys:
            continue
        editorial = history_by_key.get(key)
        if not editorial:
            continue
        published = datetime.fromisoformat(editorial["publish_date"]).astimezone(TZ)
        if published > now:
            published = now
        item = _new_item(editorial["title"], editorial["description"], row["guid"], row["image_url"], published)
        items.append(item)
        existing_today.append(item)
        existing_keys.add(key)
        feed_changed = True
        changed = True

    for sequence in range(1, goal + 1):
        if len(existing_today) >= goal:
            break
        key = daily_key(automation, day, sequence)
        if key in existing_keys:
            continue
        entry = catalog[(day.toordinal() + sequence - 1) % len(catalog)]
        image, theme = _choose_image(entry, used_images, key)
        used_images.add(image)
        title, description = _copy(automation, entry, theme, sequence, day, history + new_history)
        prefix = f"{SECTIONS[automation]}"
        stem = f"{automation}-{day:%Y%m%d}-{sequence:03d}"
        post_path = f"{prefix}/posts/{day:%Y/%m/%d}/{stem}.html"
        go_path = f"{prefix}/go/{day:%Y/%m/%d}/{stem}.html"
        post_url = f"{config['public_base_url']}/{post_path}"
        go_url = f"{config['public_base_url']}/{go_path}"
        published = max(datetime.combine(day, time.min, TZ), now - timedelta(seconds=goal - sequence))
        new_item = _new_item(title, description, go_url, image, published)
        write_if_changed(root / post_path, _page_html(config, automation, title, description, image, post_url, go_url))
        write_if_changed(root / go_path, _go_html(config, automation, entry, go_url))
        items.append(new_item)
        existing_today.append(new_item)
        existing_keys.add(key)
        feed_changed = True
        changed = True
        if key not in known_keys:
            new_registry.append({"daily_key": key, "automation": automation, "date": day.isoformat(), "guid": go_url, "post_path": post_path, "go_path": go_path, "affiliate_url": entry["affiliate_url"], "image_url": image})
            known_keys.add(key)
        if key not in history_keys:
            new_history.append({"daily_key": key, "automation": automation, "publish_date": published.isoformat(), "title": title, "description": description, "image_url": image, "guid": go_url})
            history_keys.add(key)

    # Repair a crash after feed write but before registry/history append.
    for item in existing_today:
        guid = text_of(item, "guid")
        key = key_from_guid(guid, automation, day)
        if not key:
            continue
        sequence = int(key.rsplit("|", 1)[-1])
        entry = catalog[(day.toordinal() + sequence - 1) % len(catalog)]
        stem = f"{automation}-{day:%Y%m%d}-{sequence:03d}"
        prefix = SECTIONS[automation]
        post_path = f"{prefix}/posts/{day:%Y/%m/%d}/{stem}.html"
        go_path = f"{prefix}/go/{day:%Y/%m/%d}/{stem}.html"
        title, description = text_of(item, "title"), text_of(item, "description")
        enclosure = item.find("enclosure")
        image = enclosure.get("url", "") if enclosure is not None else ""
        post_url = f"{config['public_base_url']}/{post_path}"
        if not (root / post_path).exists():
            changed |= write_if_changed(root / post_path, _page_html(config, automation, title, description, image, post_url, guid))
        if not (root / go_path).exists():
            changed |= write_if_changed(root / go_path, _go_html(config, automation, entry, guid))
        if key not in known_keys:
            new_registry.append({"daily_key": key, "automation": automation, "date": day.isoformat(), "guid": guid, "post_path": post_path, "go_path": go_path, "affiliate_url": entry["affiliate_url"], "image_url": image})
            known_keys.add(key)
            changed = True
        if key not in history_keys:
            new_history.append({"daily_key": key, "automation": automation, "publish_date": parsedate_to_datetime(text_of(item, "pubDate")).isoformat(), "title": title, "description": description, "image_url": image, "guid": guid})
            history_keys.add(key)
            changed = True

    # A crash between writing the feed and pages must be repairable without a new GUID.
    for row in registry:
        if row.get("automation") != automation or row.get("date") != day.isoformat():
            continue
        if not (root / row["go_path"]).exists():
            entry = next((entry for entry in catalog if entry["affiliate_url"] == row["affiliate_url"]), None)
            if entry:
                changed |= write_if_changed(root / row["go_path"], _go_html(config, automation, entry, row["guid"]))
    if len(existing_today) >= goal:
        original_length = len(items)
        items = [item for item in items if item_date(item) != day or key_from_guid(text_of(item, "guid"), automation, day)]
        if len(items) != original_length:
            feed_changed = True
            changed = True
    if feed_changed:
        _write_feed(feed_path, items, int(config["feed_max_items"]))
    elif (feed_path.with_name("rss.xml").read_bytes() if feed_path.with_name("rss.xml").exists() else b"") != feed_path.read_bytes():
        write_if_changed(feed_path.with_name("rss.xml"), feed_path.read_text(encoding="utf-8"))
        changed = True
    if changed:
        append_rows(registry_path, REGISTRY_FIELDS, new_registry)
        append_rows(history_path, HISTORY_FIELDS, new_history)
    changed |= _update_sitemap(root, config, [row for row in registry + new_registry if row.get("automation") == automation and row.get("date") == day.isoformat()])
    final_items = rss_items(feed_path)
    validate_no_future_pubdates(final_items, now)
    status = actual_daily_status(root, automation, day, config)
    status["changed"] = changed
    status["latest_pubdate"] = max((text_of(item, "pubDate") for item in final_items), default="")
    if status["feed_count"] < goal or not status["complete"]:
        raise ValueError(f"Lote incompleto após reparo: {automation} {status['feed_count']}/{goal}")
    return status


def validate_public_tree(root: Path, day: date | None = None) -> dict:
    day = day or datetime.now(TZ).date()
    config = load_config(root)
    result = {}
    all_guids: set[str] = set()
    for automation, section_name in SECTIONS.items():
        items = rss_items(root / section_name / "feed.xml")
        validate_no_future_pubdates(items, datetime.now(TZ))
        if len(items) > int(config["feed_max_items"]):
            raise ValueError(f"Feed acima do limite: {automation}")
        for item in items:
            guid = text_of(item, "guid")
            link = text_of(item, "link")
            if guid in all_guids or guid != link or f"/{section_name}/go/" not in guid:
                raise ValueError(f"GUID/link inválido em {automation}")
            all_guids.add(guid)
            enclosure = item.find("enclosure")
            image = enclosure.get("url", "") if enclosure is not None else ""
            if urlparse(image).hostname != "res.cloudinary.com":
                raise ValueError(f"Imagem inválida em {automation}")
            path = guid.removeprefix(config["public_base_url"] + "/")
            if not (root / path).is_file():
                raise ValueError(f"Página /go/ ausente em {automation}")
        for row in read_rows(root / "automation_runtime" / "public_registry.csv"):
            if row.get("automation") == automation and row.get("date") == day.isoformat() and not (root / row["post_path"]).is_file():
                raise ValueError(f"Página /posts/ ausente em {automation}")
        result[automation] = actual_daily_status(root, automation, day, config)
    return result
