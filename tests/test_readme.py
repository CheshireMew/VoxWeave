from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parents[1]
SVG_NAMESPACE = {"svg": "http://www.w3.org/2000/svg"}


def _theme_color(name: str) -> str:
    source = (ROOT / "src" / "voxweave" / "qml" / "Theme.qml").read_text(encoding="utf-8")
    match = re.search(rf'property color {re.escape(name)}: "(#[0-9A-Fa-f]{{6}})"', source)
    assert match is not None, f"Theme.qml does not define {name}"
    return match.group(1).upper()


def test_readme_logo_matches_desktop_title_bar_mark() -> None:
    title_bar = (ROOT / "src" / "voxweave" / "qml" / "AppTitleBar.qml").read_text(
        encoding="utf-8"
    )
    assert "Layout.preferredWidth: 19" in title_bar
    assert "Layout.preferredHeight: 19" in title_bar
    assert "radius: 5" in title_bar

    heights_match = re.search(r"model:\s*\[([^]]+)\]", title_bar)
    assert heights_match is not None
    title_bar_heights = [int(value) for value in heights_match.group(1).split(",")]

    root = ET.parse(ROOT / "assets" / "readme" / "logo.svg").getroot()
    assert root.attrib["viewBox"] == "0 0 19 19"

    rectangles = root.findall("svg:rect", SVG_NAMESPACE)
    background, bars = rectangles[0], rectangles[1:]
    assert background.attrib["width"] == "19"
    assert background.attrib["height"] == "19"
    assert background.attrib["rx"] == "5"
    assert background.attrib["fill"].upper() == _theme_color("accent")
    assert [int(float(bar.attrib["height"])) for bar in bars] == title_bar_heights
    assert all(bar.attrib["width"] == "2" for bar in bars)
    assert all(bar.attrib["rx"] == "1" for bar in bars)
    assert all(bar.attrib["fill"].upper() == _theme_color("accentInk") for bar in bars)


def test_readme_visuals_are_safe_local_svg() -> None:
    for name in ("logo.svg", "hero.svg"):
        path = ROOT / "assets" / "readme" / name
        root = ET.parse(path).getroot()
        assert root.attrib["role"] == "img"
        assert root.attrib["aria-labelledby"] == "title desc"
        assert root.find("svg:title", SVG_NAMESPACE) is not None
        assert root.find("svg:desc", SVG_NAMESPACE) is not None

        source = path.read_text(encoding="utf-8").replace(
            "http://www.w3.org/2000/svg", ""
        )
        assert not re.search(
            r"<script|<foreignObject|https?://|onload=|onclick=|data:image",
            source,
            flags=re.IGNORECASE,
        )


def test_language_readmes_use_logo_before_explanatory_visual() -> None:
    for name in ("README.md", "README.en.md", "README.ja.md"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert source.count("<!-- readme-header:start -->") == 1
        assert source.count("<!-- readme-header:end -->") == 1
        assert source.find("assets/readme/logo.svg") < source.find("assets/readme/hero.svg")
        assert "CONTRIBUTING.md" in source
        assert "LICENSE" in source


def test_star_history_has_one_remote_producer_and_all_readme_consumers() -> None:
    workflow = (ROOT / ".github" / "workflows" / "star-history.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert 'cron: "17 3 * * 1"' in workflow
    assert "contents: write" in workflow
    assert (
        "uses: CheshireMew/project-steward/.github/workflows/star-history.yml@main"
        in workflow
    )

    light_url = (
        "https://raw.githubusercontent.com/CheshireMew/VoxWeave/"
        "star-history/star-history.svg"
    )
    dark_url = (
        "https://raw.githubusercontent.com/CheshireMew/VoxWeave/"
        "star-history/star-history-dark.svg"
    )
    legal_headings = {
        "README.md": "## 许可证与第三方组件",
        "README.en.md": "## License and third-party components",
        "README.ja.md": "## ライセンスと第三者コンポーネント",
    }
    for name, legal_heading in legal_headings.items():
        source = (ROOT / name).read_text(encoding="utf-8")
        assert source.count("## Star History") == 1
        assert source.count(light_url) == 2
        assert source.count(dark_url) == 1
        assert source.index("## Star History") < source.index(legal_heading)
