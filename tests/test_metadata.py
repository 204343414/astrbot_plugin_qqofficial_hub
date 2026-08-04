from pathlib import Path

import yaml


def test_metadata_required_fields_are_nonempty_strings():
    metadata = yaml.safe_load(
        Path(__file__).parents[1].joinpath("metadata.yaml").read_text(encoding="utf-8")
    )
    for field in ("name", "desc", "version", "author"):
        assert isinstance(metadata[field], str)
        assert metadata[field].strip()


def test_config_schema_has_direct_astrbot_items():
    schema = yaml.safe_load(
        Path(__file__).parents[1].joinpath("_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(schema, dict)
    for name, item in schema.items():
        assert isinstance(item, dict), name
        assert isinstance(item.get("type"), str), name


def test_support_platforms_uses_valid_adapter_ids():
    """Must match ADAPTER_NAME_2_TYPE keys or the WebUI tag renders wrong.

    Note: this field is display-only in AstrBot v4.26.7 — it is read into
    StarMetadata and handed to the dashboard, but the event pipeline never
    consults it. Runtime gating requires filter.platform_adapter_type.
    """
    metadata = yaml.safe_load(
        Path(__file__).parents[1].joinpath("metadata.yaml").read_text(encoding="utf-8")
    )
    known = {
        "aiocqhttp", "qq_official", "qq_official_webhook", "telegram", "wecom",
        "wecom_ai_bot", "lark", "dingtalk", "discord", "slack", "kook",
        "vocechat", "weixin_official_account", "satori", "misskey", "line",
        "matrix", "weixin_oc", "mattermost", "webchat",
    }
    platforms = metadata["support_platforms"]
    assert isinstance(platforms, list) and platforms
    for item in platforms:
        assert item in known, item
    assert "qq_official" in platforms


def test_plugin_exposes_exactly_one_page():
    """AstrBot's sidebar only links a plugin's *first* page
    (usePluginSidebarItems uses ``p.pages[0]``), so a second page would have no
    entry point at all. Diagnostics therefore lives inside the editor.
    """
    pages_root = Path(__file__).parents[1] / "pages"
    names = [p.name for p in pages_root.iterdir() if p.is_dir()]
    assert names == ["panels"], f"侧边栏只会链接第一个页面，多余页面无入口: {names}"


def test_editor_hosts_the_diagnostics_drawer():
    root = Path(__file__).parents[1] / "pages" / "panels"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    assert 'id="open-diagnostics"' in html, "编辑器需有运行诊断入口"
    assert 'id="diag-modal"' in html
    assert "loadDiagnostics" in js


def test_every_page_has_a_localised_title():
    """Without i18n the tab shows the raw directory name, e.g. 'zz-diagnostics'."""
    import json
    root = Path(__file__).parents[1]
    pages = {p.name for p in (root / "pages").iterdir() if p.is_dir()}
    for locale_file in (root / ".astrbot-plugin" / "i18n").glob("*.json"):
        data = json.loads(locale_file.read_text(encoding="utf-8"))
        titled = set(data.get("pages", {}))
        assert pages <= titled, f"{locale_file.name} 缺少标题: {pages - titled}"


def test_every_page_has_an_entry_file():
    """A page directory without index.html is silently skipped by AstrBot."""
    pages_root = Path(__file__).parents[1] / "pages"
    for page in (p for p in pages_root.iterdir() if p.is_dir()):
        assert (page / "index.html").is_file(), f"{page.name} 缺少 index.html"


def test_every_module_used_in_main_is_imported():
    """Guards against NameError at plugin load.

    A missing import here is invisible in review and fatal at startup: the Hub
    shipped with `image_host.ImageHost(...)` but no `import image_host`, so
    every dependent plugin reported "Hub 未安装" until it was noticed. Checked
    with the AST rather than by importing, because importing main.py needs the
    whole AstrBot runtime.
    """
    import ast

    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "main.py").read_text("utf-8"))

    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            bound.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            bound.update((a.asname or a.name).split(".")[0] for a in node.names)

    # Names used as `something.attr` that match a Hub submodule on disk.
    submodules = {p.stem for p in (root / "qqofficial_hub").glob("*.py")}
    used = {
        node.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    missing = (used & submodules) - bound
    assert not missing, f"main.py 用了却没 import: {sorted(missing)}"
