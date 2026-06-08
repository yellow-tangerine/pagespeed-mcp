import os
import httpx
from fastmcp import FastMCP

API_KEY = os.environ["PAGESPEED_API_KEY"]
BASE_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

mcp = FastMCP("pagespeed-insights")

@app.get("/health")
async def health():
    return {"status": "ok"}


async def _fetch(url: str, strategy: str, categories: list[str]) -> dict:
    params = [("url", url), ("key", API_KEY), ("strategy", strategy)]
    for cat in categories:
        params.append(("category", cat))
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.get(BASE_URL, params=params)
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def analyse_page(url: str, strategy: str = "mobile") -> dict:
    """Full Lighthouse audit across performance, accessibility, best-practices, SEO.

    Args:
        url: Full URL including https://
        strategy: "mobile" or "desktop"
    """
    data = await _fetch(
        url, strategy,
        ["performance", "accessibility", "best-practices", "seo"],
    )
    lh = data["lighthouseResult"]
    cats = lh["categories"]
    audits = lh["audits"]

    def score(c):
        v = cats.get(c, {}).get("score")
        return round(v * 100) if v is not None else None

    return {
        "url": url,
        "strategy": strategy,
        "scores": {c: score(c) for c in
                   ["performance", "accessibility", "best-practices", "seo"]},
        "core_web_vitals": {
            "fcp": audits.get("first-contentful-paint", {}).get("displayValue"),
            "lcp": audits.get("largest-contentful-paint", {}).get("displayValue"),
            "cls": audits.get("cumulative-layout-shift", {}).get("displayValue"),
            "tbt": audits.get("total-blocking-time", {}).get("displayValue"),
            "si":  audits.get("speed-index", {}).get("displayValue"),
            "tti": audits.get("interactive", {}).get("displayValue"),
        },
        "fetch_time": lh.get("fetchTime"),
    }


@mcp.tool()
async def get_recommendations(url: str, strategy: str = "mobile") -> dict:
    """Prioritised performance opportunities sorted by potential savings.

    Args:
        url: Full URL including https://
        strategy: "mobile" or "desktop"
    """
    data = await _fetch(url, strategy, ["performance"])
    audits = data["lighthouseResult"]["audits"]

    opportunities = []
    for aid, audit in audits.items():
        details = audit.get("details", {})
        if details.get("type") != "opportunity":
            continue
        savings_ms = details.get("overallSavingsMs", 0) or 0
        if savings_ms <= 0:
            continue
        opportunities.append({
            "id": aid,
            "title": audit.get("title"),
            "description": audit.get("description"),
            "savings_ms": savings_ms,
            "savings_kb": round((details.get("overallSavingsBytes", 0) or 0) / 1024),
            "score": audit.get("score"),
        })

    opportunities.sort(key=lambda x: x["savings_ms"], reverse=True)
    return {"url": url, "strategy": strategy, "opportunities": opportunities[:15]}


@mcp.tool()
async def get_element_analysis(url: str, strategy: str = "mobile") -> dict:
    """DOM elements responsible for poor Core Web Vitals — useful for the
    desktop-vs-mobile conversion gap diagnosis.

    Args:
        url: Full URL including https://
        strategy: "mobile" or "desktop"
    """
    data = await _fetch(url, strategy, ["performance"])
    audits = data["lighthouseResult"]["audits"]

    result = {
        "url": url, "strategy": strategy,
        "lcp_element": None, "cls_culprits": [],
        "lazy_loaded_lcp_warning": False,
    }

    lcp_items = audits.get("largest-contentful-paint-element", {}) \
                      .get("details", {}).get("items", [])
    if lcp_items:
        first = lcp_items[0]
        nested = first.get("items", [first]) if isinstance(first, dict) else [first]
        if nested:
            node = nested[0].get("node", {}) if isinstance(nested[0], dict) else {}
            result["lcp_element"] = {
                "selector": node.get("selector"),
                "snippet": node.get("snippet"),
                "node_label": node.get("nodeLabel"),
            }

    for item in (audits.get("layout-shift-elements", {})
                       .get("details", {}).get("items", []))[:10]:
        node = item.get("node", {})
        result["cls_culprits"].append({
            "selector": node.get("selector"),
            "snippet": node.get("snippet"),
            "score": item.get("score"),
        })

    if audits.get("lcp-lazy-loaded", {}).get("score") == 0:
        result["lazy_loaded_lcp_warning"] = True

    return result


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")